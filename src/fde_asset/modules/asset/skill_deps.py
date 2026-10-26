"""技能依赖检查（技能依赖的两层处理，2026-10-26）。

- 第 1 层：运行镜像预装了一批常用库（见 PREINSTALLED，和 fde-runtime 的 config/python-libs.txt 一致），
  技能直接用，不用声明；
- 第 2 层：其余的依赖写进技能目录的 requirements.txt。评审通过后平台在 Agent 机器上预装，
  Agent 执行时自动能 import。共享技能带新依赖要评审人确认；个人技能免确认，但有数量限额。

清单规则和 fde-server 的 fde/orchestrator/skill_deps.py 保持一致（两边测试用同一组对照值）：
只接受「包名」「包名==版本」这类写法，网址、本地路径、选项一律拒绝。
"""

from __future__ import annotations

import ast
from pathlib import PurePosixPath
import re
import sys
from typing import Any

REQUIREMENTS_FILE = "requirements.txt"
#: 键算法和 fde-server、fde-runtime 一致（三边测试用同一组对照值）
PYTHON_TAG = "cp311-debian12"
KEY_PREFIX = "fde-skill-deps/v1"
#: 每人个人技能的依赖合计上限（按 worker 回报的安装大小算）
PERSONAL_BYTES_LIMIT = 1024 * 1024 * 1024
MAX_LINES = 30
#: 个人技能里带依赖的最多几个
PERSONAL_LIMIT = 10

#: 运行镜像预装的库：发行名 -> 导入名
PREINSTALLED: dict[str, tuple[str, ...]] = {
    "python-pptx": ("pptx",),
    "python-docx": ("docx",),
    "openpyxl": ("openpyxl",),
    "xlsxwriter": ("xlsxwriter",),
    "pypdf": ("pypdf",),
    "reportlab": ("reportlab",),
    "numpy": ("numpy",),
    "pandas": ("pandas",),
    "matplotlib": ("matplotlib", "mpl_toolkits", "pylab"),
    "pillow": ("PIL",),
    "lxml": ("lxml",),
}
#: 预装库顺带装上的（不用声明也能用）
BUNDLED_MODULES = {
    "dateutil", "pytz", "tzdata", "six", "et_xmlfile", "contourpy", "cycler",
    "fontTools", "kiwisolver", "packaging", "pyparsing", "typing_extensions", "chardet",
}  # fmt: skip
#: 发行名和导入名不一样的常见库
IMPORT_NAMES = {
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "scikit-learn": "sklearn",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "python-dateutil": "dateutil",
    "pymupdf": "fitz",
    "attrs": "attr",
    "protobuf": "google",
    "pyjwt": "jwt",
    "python-dotenv": "dotenv",
    **{name: modules[0] for name, modules in PREINSTALLED.items()},
}

_REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?P<extras>\[[A-Za-z0-9,._ -]+\])?"
    r"(?P<spec>(?:\s*(?:==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+)"
    r"(?:\s*,\s*(?:==|>=|<=|~=|!=|<|>)\s*[A-Za-z0-9.*+!_-]+)*)?$"
)


class RequirementsError(ValueError):
    pass


def parse_requirements(text: str) -> list[str]:
    lines: dict[str, str] = {}
    for number, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = _REQUIREMENT.match(line)
        if match is None:
            raise RequirementsError(
                f"requirements.txt 第 {number} 行写法不支持：{line[:60]}"
                "（只能写「包名」或「包名==版本」，不能写网址、路径和选项）"
            )
        name = re.sub(r"[-_.]+", "-", match["name"]).lower()
        if name in lines:
            raise RequirementsError(f"requirements.txt 里 {name} 写了两次")
        extras = (match["extras"] or "").replace(" ", "").lower()
        lines[name] = name + extras + re.sub(r"\s+", "", match["spec"] or "")
    if len(lines) > MAX_LINES:
        raise RequirementsError(f"依赖最多 {MAX_LINES} 个，现在有 {len(lines)} 个")
    return [lines[name] for name in sorted(lines)]


def deps_key(requirements: list[str], python_tag: str = PYTHON_TAG) -> str:
    import hashlib

    payload = "\n".join([KEY_PREFIX, python_tag, *requirements])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def requirement_name(line: str) -> str:
    return re.split(r"[\[=<>!~]", line, maxsplit=1)[0]


def _imports(text: str) -> set[str]:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set()
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module.split(".", 1)[0])
    return found


def analyze(files: dict[str, str]) -> dict[str, Any]:
    """依赖检查报告：声明了哪些、哪些是预装的、哪些要新装，脚本里用了却没声明的。"""
    declared: list[str] = []
    error = ""
    if REQUIREMENTS_FILE in files:
        try:
            declared = parse_requirements(files[REQUIREMENTS_FILE])
        except RequirementsError as exc:
            error = str(exc)
    names = [requirement_name(line) for line in declared]
    local = set()
    for path in files:
        parts = PurePosixPath(path).parts
        local.update(PurePosixPath(part).stem for part in parts)
    known = (
        set(sys.stdlib_module_names)
        | {module for modules in PREINSTALLED.values() for module in modules}
        | BUNDLED_MODULES
        | local
        | {IMPORT_NAMES.get(name, name.replace("-", "_")) for name in names}
        | {name.replace("-", "_") for name in names}
        | {"__future__"}
    )
    used: set[str] = set()
    for path, text in files.items():
        if path.endswith(".py") or text.startswith("#!/usr/bin/env python"):
            used |= _imports(text)
    return {
        "key": deps_key(declared) if declared else "",
        "declared": declared,
        "preinstalled": [line for line in declared if requirement_name(line) in PREINSTALLED],
        "new": [line for line in declared if requirement_name(line) not in PREINSTALLED],
        "undeclared_imports": sorted(name for name in used if name not in known),
        "error": error,
    }


# ---------- 安装状态（fde-server worker 回报）----------


def wanted(engine) -> list[dict[str, Any]]:
    """已入库技能声明的依赖（去重）：worker 据此在评审通过后就预装，不等第一次用到。"""
    import json

    from sqlalchemy import select

    from fde_asset.core.db import harvest_candidates

    with engine.connect() as conn:
        rows = conn.execute(
            select(harvest_candidates.c.checks_json).where(
                harvest_candidates.c.kind == "Skill", harvest_candidates.c.status == "merged"
            )
        ).fetchall()
    found: dict[str, list[str]] = {}
    for row in rows:
        deps = ((json.loads(row.checks_json or "{}").get("skill") or {}).get("deps")) or {}
        declared = deps.get("declared") or []
        if declared and not deps.get("error"):
            found[deps_key(declared)] = declared
    return [{"key": key, "requirements": value} for key, value in sorted(found.items())]


def record_status(engine, items: list[dict[str, Any]]) -> int:
    """worker 回报安装状态：键要和清单对得上（防止乱写），按键覆盖。"""
    import json
    from datetime import datetime, timezone

    from sqlalchemy import delete, insert

    from fde_asset.core.db import skill_deps_status

    saved = 0
    with engine.begin() as conn:
        for item in items[:500]:
            try:
                requirements = parse_requirements("\n".join(item.get("requirements") or []))
            except RequirementsError:
                continue
            key = deps_key(requirements)
            if not requirements or item.get("key") != key:
                continue
            state = str(item.get("state") or "missing")
            if state not in {"ready", "failed", "installing", "missing"}:
                continue
            conn.execute(delete(skill_deps_status).where(skill_deps_status.c.key == key))
            conn.execute(
                insert(skill_deps_status).values(
                    key=key,
                    requirements_json=json.dumps(requirements),
                    state=state,
                    bytes=max(0, int(item.get("bytes") or 0)),
                    reason=str(item.get("reason") or "")[:500],
                    updated_at=datetime.now(timezone.utc),
                )
            )
            saved += 1
    return saved


def status_of(engine, key: str) -> dict[str, Any] | None:
    from sqlalchemy import select

    from fde_asset.core.db import skill_deps_status

    if not key:
        return None
    with engine.connect() as conn:
        row = conn.execute(select(skill_deps_status).where(skill_deps_status.c.key == key)).first()
    if row is None:
        return None
    return {
        "state": row.state,
        "bytes": row.bytes,
        "reason": row.reason,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def personal_bytes(engine, keys: set[str]) -> int:
    """这些依赖键已装好的大小合计（同一份依赖只算一次）。"""
    from sqlalchemy import select

    from fde_asset.core.db import skill_deps_status

    if not keys:
        return 0
    with engine.connect() as conn:
        rows = conn.execute(
            select(skill_deps_status.c.bytes).where(skill_deps_status.c.key.in_(sorted(keys)))
        ).fetchall()
    return sum(int(row.bytes or 0) for row in rows)
