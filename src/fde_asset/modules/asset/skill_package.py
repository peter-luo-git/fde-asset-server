"""技能包：开头信息（name、description）、整目录导入、安全检查、编写检查。

技能大多是一个目录：`SKILL.md` + 脚本、模板、参考文件，所以支持整目录（文件夹或 zip）进来，
也支持从公开技能库安装（见 skill_sources.py）。不管从哪来，入库前都过同一套检查：

1. 文件：大小、数量、路径（不能指到目录外、不能有符号链接）、可执行二进制、压缩包套压缩包；
2. 开头信息：必须有 name 和 description，name 和资产标识一致；
3. 文字：隐藏字符、大段编码内容、提示注入（"忽略之前的指令"一类）；
4. 脚本：用什么解释器、镜像里有没有、用到哪些敏感操作（联网、删文件、提权、读凭据、动态执行、
   下载执行、装依赖），高风险的默认不给执行权限，要评审人勾选；
5. 试跑：在 Agent 同款运行镜像里断网跑一遍（skill_trials.py），没过的不给执行权限、明确提示。

第一版只收文本文件（脚本、Markdown、模板、数据样例）；图片、PDF 这类二进制文件先不收。
"""

from __future__ import annotations

import base64
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

MAX_FILES = 200
MAX_TOTAL_BYTES = 5 * 1024 * 1024
MAX_FILE_BYTES = 1024 * 1024
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024

#: Agent 运行镜像里有的解释器（fde-runtime 的 Dockerfile：node 22、python3、bash/sh）
AVAILABLE_INTERPRETERS = {"python3", "python", "bash", "sh", "node"}
SCRIPT_SUFFIXES = {
    ".py": "python3",
    ".sh": "bash",
    ".bash": "bash",
    ".js": "node",
    ".mjs": "node",
    ".cjs": "node",
    ".ts": "node",
    ".rb": "ruby",
    ".pl": "perl",
    ".ps1": "pwsh",
}
ARCHIVE_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".whl")
#: 可执行二进制的文件头：ELF、Mach-O、Windows PE
BINARY_MAGIC = (b"\x7fELF", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"MZ")

#: 脚本里的敏感操作：(类别, 说明, 级别, 正则)
SCRIPT_RISKS: tuple[tuple[str, str, str, re.Pattern[str]], ...] = (
    (
        "download_exec",
        "下载后直接执行",
        "high",
        re.compile(r"(curl|wget)[^\n|]*\|\s*(ba)?sh|iex\s*\(|Invoke-Expression", re.I),
    ),
    (
        "privilege",
        "提权或改权限",
        "high",
        re.compile(r"\bsudo\b|\bsu\s+-|\bchmod\b|\bchown\b|setuid", re.I),
    ),
    (
        "credentials",
        "读凭据或环境变量",
        "high",
        re.compile(
            r"~/\.ssh|id_rsa|\.aws/|\.netrc|\.docker/config|os\.environ|getenv\(|process\.env|"
            r"\$\{?(GITEA|GITHUB|AWS|OPENAI|ANTHROPIC|API)_?[A-Z_]*(TOKEN|KEY|SECRET)",
            re.I,
        ),
    ),
    (
        "delete",
        "删除文件或目录",
        "high",
        re.compile(
            r"rm\s+-[a-z]*r[a-z]*f|rm\s+-[a-z]*f[a-z]*r|shutil\.rmtree|fs\.rm(Sync)?\(|rimraf", re.I
        ),
    ),
    (
        "network",
        "联网",
        "medium",
        re.compile(
            r"\bcurl\b|\bwget\b|requests\.(get|post|put|delete)|urllib|http\.client|socket\.|"
            r"\bfetch\(|axios|https?://",
            re.I,
        ),
    ),
    (
        "dynamic_exec",
        "动态执行代码或外部命令",
        "medium",
        re.compile(r"\beval\(|\bexec\(|subprocess|os\.system|child_process|new Function\(", re.I),
    ),
    (
        "install",
        "安装依赖",
        "medium",
        re.compile(r"pip3? install|npm (i|install)\b|apt(-get)? install|yarn add|pnpm add", re.I),
    ),
)

#: 文字里的提示注入：命中的段落标给评审人
PROMPT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "要求忽略原有指令",
        re.compile(
            r"忽略(之前|以上|前面|所有|上面)的?(所有)?(指令|规则|要求|提示)|"
            r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)|disregard\s+(all|previous)",
            re.I,
        ),
    ),
    (
        "要求瞒着用户",
        re.compile(r"不要(告诉|让)用户|别让用户知道|do not (tell|inform) the user", re.I),
    ),
    (
        "要求绕过限制",
        re.compile(r"绕过(权限|审批|限制|沙盒)|bypass (the )?(sandbox|approval|permission)", re.I),
    ),
    (
        "涉及密钥外传",
        re.compile(
            r"(密钥|token|口令|密码|凭据|api[_ ]?key)[^\n]{0,30}(发送|上传|写进回复|贴出|post|send)|"
            r"(发送|上传|send|post)[^\n]{0,30}(密钥|token|口令|密码|凭据|api[_ ]?key)",
            re.I,
        ),
    ),
    ("读取敏感路径", re.compile(r"~/\.ssh|/etc/passwd|id_rsa|\.aws/credentials", re.I)),
    (
        "冒充系统提示",
        re.compile(r"system prompt|系统提示词|you are now|你现在是(一个)?不受限", re.I),
    ),
)
HIDDEN_CHARS = re.compile("[​-‏‪-‮⁠-⁤⁦-⁩]")
LONG_ENCODED = re.compile(r"[A-Za-z0-9+/=]{240,}")


class SkillPackageError(ValueError):
    pass


@dataclass
class Package:
    """整理好的技能包：{相对路径: 文本}，以及没收进来的文件和原因。"""

    files: dict[str, str] = field(default_factory=dict)
    rejected: list[dict[str, str]] = field(default_factory=list)


def parse_frontmatter(text: str) -> dict[str, str] | None:
    """SKILL.md 开头 `---` 之间的 name、description 等；没有开头返回 None。"""
    match = re.match(r"\A﻿?---\r?\n(.*?)\r?\n---[ \t]*(\r?\n|\Z)", text or "", re.S)
    if match is None:
        return None
    try:
        import yaml

        value = yaml.safe_load(match.group(1)) or {}
    except Exception:  # noqa: BLE001 - 开头写坏了按没有处理，由检查报出来
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def with_frontmatter(body: str, *, name: str, description: str) -> str:
    """没有开头信息的正文补上开头（上传历史文档、旧草稿用）。"""
    if parse_frontmatter(body) is not None:
        return body
    safe = description.replace("\n", " ").strip() or "<一句话说明这个技能在什么时候用、能做什么>"
    return f"---\nname: {name}\ndescription: {safe}\n---\n\n{body.lstrip()}"


def frontmatter_findings(text: str, *, name: str) -> list[dict[str, str]]:
    """开头信息检查：缺了 Agent 就发现不了这个技能，所以是硬伤（挡提交）。"""
    front = parse_frontmatter(text)
    if front is None:
        return [
            {
                "code": "skill_frontmatter_missing",
                "message": "SKILL.md 开头要有 `---` 包起来的 name 和 description，Agent 靠它们发现技能",
            }
        ]
    findings = []
    actual = front.get("name", "").strip()
    if not actual:
        findings.append({"code": "skill_name_missing", "message": "SKILL.md 开头缺 name"})
    elif actual != name:
        findings.append(
            {
                "code": "skill_name_mismatch",
                "message": f"SKILL.md 开头的 name（{actual}）要和资产标识（{name}）一致",
            }
        )
    description = front.get("description", "").strip()
    if not description or description.startswith("<"):
        findings.append(
            {
                "code": "skill_description_missing",
                "message": "SKILL.md 开头缺 description（写清什么时候用）",
            }
        )
    elif len(description) > MAX_DESCRIPTION:
        findings.append(
            {
                "code": "skill_description_too_long",
                "message": f"description 最多 {MAX_DESCRIPTION} 字",
            }
        )
    return findings


def writing_hints(text: str) -> list[dict[str, str]]:
    """写作提醒（不挡提交）：description 有没有说清"什么时候用"、正文是不是太长。"""
    front = parse_frontmatter(text) or {}
    description = front.get("description", "")
    hints = []
    if description and not re.search(
        r"在.{0,40}(时|的时候)|当|用于|适用|use (it )?when|when ", description, re.I
    ):
        hints.append(
            {
                "level": "warning",
                "message": "description 最好写清什么时候用（如「在……时使用」），模型靠这句判断要不要加载",
            }
        )
    if text.count("\n") > 500:
        hints.append(
            {
                "level": "info",
                "message": "SKILL.md 超过 500 行；细节可以拆到 references/ 里，按需读取",
            }
        )
    return hints


def _clean_path(raw: str) -> str | None:
    path = raw.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    parts = PurePosixPath(path).parts
    if not path or path.startswith("/") or any(p in ("..", "") for p in parts) or "\x00" in path:
        return None
    return "/".join(parts)


def _strip_wrapper(paths: list[str]) -> str:
    """压缩包里常多包一层目录（`my-skill/SKILL.md`）：只有一层且里面有 SKILL.md 时去掉它。"""
    if "SKILL.md" in paths:
        return ""
    tops = {p.split("/", 1)[0] for p in paths}
    if len(tops) == 1:
        top = next(iter(tops))
        if f"{top}/SKILL.md" in paths:
            return top + "/"
    return ""


def normalize(raw: dict[str, bytes | None], *, symlinks: set[str] | None = None) -> Package:
    """原始文件 → 技能包。不收的文件记原因，不悄悄丢。"""
    symlinks = symlinks or set()
    cleaned: dict[str, bytes] = {}
    package = Package()
    for path, data in raw.items():
        clean = _clean_path(path)
        if clean is None:
            package.rejected.append({"path": path, "reason": "路径不安全（绝对路径或指到目录外）"})
            continue
        if path in symlinks:
            package.rejected.append({"path": clean, "reason": "不收符号链接"})
            continue
        if (
            clean.split("/")[-1] in {".DS_Store", "Thumbs.db"}
            or "/__pycache__/" in f"/{clean}/"
            or clean.startswith(".git/")
        ):
            continue
        cleaned[clean] = data
    prefix = _strip_wrapper(sorted(cleaned))
    if prefix:
        cleaned = {p[len(prefix) :]: d for p, d in cleaned.items() if p.startswith(prefix)}
    if "SKILL.md" not in cleaned:
        raise SkillPackageError("技能目录最外层要有 SKILL.md")
    if len(cleaned) > MAX_FILES:
        raise SkillPackageError(f"文件太多：最多 {MAX_FILES} 个")
    total = 0
    for path, data in sorted(cleaned.items()):
        if data is None or len(data) > MAX_FILE_BYTES:
            package.rejected.append(
                {"path": path, "reason": f"单个文件超过 {MAX_FILE_BYTES // 1024}KB"}
            )
            continue
        if path.lower().endswith(ARCHIVE_SUFFIXES):
            package.rejected.append({"path": path, "reason": "不收压缩包里套的压缩包"})
            continue
        if data.startswith(BINARY_MAGIC):
            package.rejected.append({"path": path, "reason": "不收可执行的二进制文件"})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            package.rejected.append(
                {"path": path, "reason": "暂时只收文本文件（图片、PDF 等二进制先不收）"}
            )
            continue
        if "\x00" in text:
            package.rejected.append(
                {"path": path, "reason": "暂时只收文本文件（图片、PDF 等二进制先不收）"}
            )
            continue
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise SkillPackageError(
                f"技能目录太大：文本文件合计最多 {MAX_TOTAL_BYTES // 1024 // 1024}MB"
            )
        package.files[path] = text
    return package


def from_zip(content: bytes) -> Package:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise SkillPackageError("不是有效的 zip 文件") from None
    entries = [info for info in archive.infolist() if not info.is_dir()]
    if len(entries) > MAX_FILES * 2:
        raise SkillPackageError(f"文件太多：最多 {MAX_FILES} 个")
    raw: dict[str, bytes | None] = {}
    symlinks: set[str] = set()
    budget = MAX_TOTAL_BYTES * 4
    for info in entries:
        if info.file_size > MAX_FILE_BYTES:
            raw[info.filename] = None  # 不解开，下面按超大文件拒收
            continue
        budget -= info.file_size
        if budget < 0:
            raise SkillPackageError("zip 解开以后太大")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            symlinks.add(info.filename)
        raw[info.filename] = archive.read(info)
    return normalize(raw, symlinks=symlinks)


def from_files(files: dict[str, str]) -> Package:
    """浏览器选文件夹上传：{相对路径: base64}。"""
    raw: dict[str, bytes] = {}
    for path, encoded in files.items():
        try:
            raw[str(path)] = base64.b64decode(str(encoded), validate=True)
        except ValueError:
            raise SkillPackageError(f"{path} 的内容不是 base64") from None
    return normalize(raw)


def interpreter_of(path: str, text: str) -> str:
    first = text.split("\n", 1)[0]
    if first.startswith("#!"):
        parts = first[2:].strip().split()
        if parts:
            program = parts[-1] if parts[0].endswith("/env") and len(parts) > 1 else parts[0]
            return program.rsplit("/", 1)[-1]
    return SCRIPT_SUFFIXES.get(PurePosixPath(path).suffix.lower(), "")


def is_script(path: str, text: str) -> bool:
    return PurePosixPath(path).suffix.lower() in SCRIPT_SUFFIXES or text.startswith("#!")


def scan(files: dict[str, str]) -> dict[str, Any]:
    """安全检查报告：给评审人看，也决定脚本默认给不给执行权限。"""
    scripts = []
    prompt_findings = []
    hidden = []
    for path, text in sorted(files.items()):
        lines = text.splitlines()
        if HIDDEN_CHARS.search(text):
            line = next(i + 1 for i, row in enumerate(lines) if HIDDEN_CHARS.search(row))
            hidden.append(
                {"path": path, "line": line, "reason": "含隐藏字符（零宽或改变显示方向的字符）"}
            )
        encoded = LONG_ENCODED.search(text)
        if encoded:
            line = text[: encoded.start()].count("\n") + 1
            hidden.append(
                {"path": path, "line": line, "reason": "含大段编码内容，看不出写的是什么"}
            )
        if is_script(path, text):
            interpreter = interpreter_of(path, text)
            risks = []
            for category, label, level, pattern in SCRIPT_RISKS:
                found = [i + 1 for i, row in enumerate(lines) if pattern.search(row)]
                if found:
                    risks.append(
                        {"category": category, "label": label, "level": level, "lines": found[:10]}
                    )
            level = (
                "high"
                if any(r["level"] == "high" for r in risks)
                else ("medium" if risks else "low")
            )
            scripts.append(
                {
                    "path": path,
                    "interpreter": interpreter,
                    "interpreter_available": interpreter in AVAILABLE_INTERPRETERS,
                    "risks": risks,
                    "level": level,
                }
            )
        elif path.lower().endswith((".md", ".txt", ".markdown")) or path == "SKILL.md":
            for i, row in enumerate(lines):
                for label, pattern in PROMPT_RULES:
                    if pattern.search(row):
                        prompt_findings.append(
                            {"path": path, "line": i + 1, "rule": label, "text": row.strip()[:200]}
                        )
    level = "low"
    if prompt_findings or hidden or any(s["level"] == "high" for s in scripts):
        level = "high"
    elif any(s["level"] == "medium" for s in scripts) or any(
        not s["interpreter_available"] for s in scripts
    ):
        level = "medium"
    return {
        "level": level,
        "files": [{"path": p, "bytes": len(t.encode("utf-8"))} for p, t in sorted(files.items())],
        "scripts": scripts,
        "prompt_findings": prompt_findings[:50],
        "hidden": hidden[:50],
    }


def default_executables(report: dict[str, Any], trial: dict[str, Any] | None) -> list[str]:
    """默认给执行权限的脚本：没有高风险、镜像里有解释器、试跑通过。其余要评审人勾选（试跑没过的不能勾）。"""
    if not trial or trial.get("status") != "passed":
        return []
    return [
        s["path"]
        for s in report.get("scripts", [])
        if s["level"] != "high" and s["interpreter_available"]
    ]


def allowed_to_tick(report: dict[str, Any], trial: dict[str, Any] | None) -> list[str]:
    """评审人能勾选给执行权限的脚本：试跑通过、镜像里有解释器的。"""
    if not trial or trial.get("status") != "passed":
        return []
    return [s["path"] for s in report.get("scripts", []) if s["interpreter_available"]]
