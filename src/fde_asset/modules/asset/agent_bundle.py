"""给平台的 Agent 资产包：某个项目的 Agent 该带的资产，拼成一份文件清单。

平台在 Agent 启动前来要这份清单，自己校验后整理成只读目录挂进运行容器。
资产在仓库里按编号放、分属不同的可见范围；这里按「这个项目看得见、关联了哪些」挑出来，
摆成平台认的目录形状：

    rules/<两位序号>-<名字>.md
    skills/<名字>/SKILL.md、asset.yaml 和技能自带的其他文件
    knowledge/cases|solutions|implementations|experiences/<名字>/README.md、asset.yaml
    knowledge/sops/<名字>/SOP.md、asset.yaml

名字是挂载名：平时就是资产的标识，同一个项目里撞名的那份加了负责人。
应用不放进包里：它是拿来跑的，不是给 Agent 读的。

每份包按内容算出一个版本号并存档。平台记下一次运行用的是哪个版本，重试时按版本号
来取同一份，不会因为中途有新资产入库而变样。

平台每轮对话都来要一次。带上上次拿到的版本号时，先只查库、看仓库提交（十几毫秒），
和上次拼包时一样就回一句「没变」，不再逐个读文件（文件越多越慢）。见 `unchanged`。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.engine import Engine

from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.modules.asset.snapshot import Selection, _renamed, select_assets
from fde_asset.platform.repo.ports import RepoRef

#: 类型 → 包里的目录。规范单独处理（一份一个文件）；应用是拿来跑的，不给 Agent
BUNDLE_DIR = {
    "Skill": "skills",
    "Case": "knowledge/cases",
    "Solution": "knowledge/solutions",
    "Implementation": "knowledge/implementations",
    "Experience": "knowledge/experiences",
    # 流程也放在 knowledge/ 下：运行镜像里校验资产目录的插件只认 rules、skills、knowledge
    # 三个顶层目录，多出一个顶层目录 Agent 就起不来
    "Sop": "knowledge/sops",
}
_RULE_NAME = re.compile(r"^[0-9]{2}-[a-z0-9]+(-[a-z0-9]+)*$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
#: 平台认的行业（和 fde-server 里校验用的那份一致）
PLATFORM_INDUSTRIES = frozenset(
    {
        "insurance",
        "banking",
        "securities",
        "manufacturing",
        "retail",
        "energy",
        "government",
        "healthcare",
        "general",
        "other",
    }
)
#: 平台校验 asset.yaml 时不认识、也用不上的字段
_DROP_METADATA = ("id",)
_DROP_SPEC = ("visibility", "attachments")


#: 版本键 → 拼出来的版本号。键由这次选中了哪些资产（整行）、各叫什么挂载名、
#: 仓库现在的提交算出：这些都没变，拼出来的包就一模一样。只放内存，重启后第一次照常拼
_KNOWN: OrderedDict[str, str] = OrderedDict()
_KNOWN_LIMIT = 1024
_KNOWN_LOCK = threading.Lock()
#: 拼包的规则（目录形状、改写 asset.yaml 的方式）变了就加一，旧的键全部作废
_KEY_FORMAT = 2  # 2：规范和流程不再按类型必带，改看必选（2026-10-17）


class BundleNotFound(LookupError):
    pass


def _heads(repo_port: Any, chosen: Selection) -> dict[str, str]:
    return {
        name: repo_port.get_head(RepoRef(name=name))
        for name in sorted({row.repo for row in chosen.rows})
    }


def _selection_key(chosen: Selection, heads: dict[str, str], request: dict[str, str]) -> str:
    material = {
        "format": _KEY_FORMAT,
        "request": request,
        "rows": [dict(row._mapping) for row in chosen.rows],
        "mounted": chosen.mounted,
        "not_carried": chosen.not_carried,
        "heads": heads,
    }
    encoded = json.dumps(material, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def unchanged(engine: Engine, repo_port: Any, known_revision: str, **request: str) -> bool:
    """平台手上那份 `known_revision` 是不是就是现在该带的：只查库和仓库提交，不读文件。

    拿不准（没拼过、服务重启过、选中的资产或仓库提交有任何变化）一律算变了，照常拼一份。
    """
    if not _REVISION.match(known_revision or ""):
        return False
    chosen = _select(engine, request)
    key = _selection_key(chosen, _heads(repo_port, chosen), request)
    with _KNOWN_LOCK:
        return _KNOWN.get(key) == known_revision


def _select(engine: Engine, request: dict[str, str]) -> Selection:
    agent_key = request.get("agent_key", "")
    return select_assets(
        engine,
        department_code=request.get("department_code", ""),
        engagement_slug=request.get("engagement_slug", ""),
        customer_code=request.get("customer_code", ""),
        target_type=request.get("target_type", ""),
        target_id=request.get("target_id", ""),
        also_targets=(("agent", agent_key),) if agent_key else (),
        username=request.get("username", ""),
        skill_names=tuple(name for name in str(request.get("sop_skills", "")).split(",") if name),
    )


def _remember(key: str, revision: str) -> None:
    with _KNOWN_LOCK:
        _KNOWN[key] = revision
        _KNOWN.move_to_end(key)
        while len(_KNOWN) > _KNOWN_LIMIT:
            _KNOWN.popitem(last=False)


@dataclass
class AgentBundle:
    revision: str
    files: dict[str, bytes]
    #: 包里每个名字对应哪份资产的哪个版本
    assets: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    def as_payload(self) -> dict[str, Any]:
        return {
            "revision": self.revision,
            "files": {
                path: base64.b64encode(content).decode("ascii")
                for path, content in sorted(self.files.items())
            },
            "assets": self.assets,
            "skipped": self.skipped,
        }


def _platform_industries(value: Any) -> list[str]:
    """行业给平台的写法：平台要求至少一项，且只认固定的那几个。

    资产中心里行业可以不填（模板默认就是空的），意思是不分行业，对应平台的 general；
    平台不认识的行业归到 other。不这样转，没填行业的资产会被平台整份丢掉。
    """
    listed = [str(item) for item in value] if isinstance(value, list) else []
    mapped = [item if item in PLATFORM_INDUSTRIES else "other" for item in listed]
    return list(dict.fromkeys(mapped)) or ["general"]


def _platform_manifest(raw: bytes, mount: str) -> bytes:
    """asset.yaml 给平台的样子：名字用挂载名，去掉平台不认识的字段。"""
    document = yaml.safe_load(raw.decode("utf-8", errors="replace"))
    if not isinstance(document, dict):
        return raw
    metadata = document.get("metadata")
    spec = document.get("spec")
    if isinstance(metadata, dict):
        for key in _DROP_METADATA:
            metadata.pop(key, None)
        metadata["name"] = mount
    if isinstance(spec, dict):
        for key in _DROP_SPEC:
            spec.pop(key, None)
        # 资产之间的关联对 Agent 没有用，平台也只接受空的
        spec["relations"] = []
        spec["industry"] = _platform_industries(spec.get("industry"))
    return yaml.safe_dump(document, allow_unicode=True, sort_keys=False).encode("utf-8")


def _rule_file_name(mount: str, taken: set[str]) -> str:
    """平台要求规范文件名以两位序号开头（决定注入顺序）；没有序号的排在中间。"""
    name = mount if _RULE_NAME.match(mount) else f"50-{mount}"
    while name in taken:
        name += "-x"
    taken.add(name)
    return name


def build_bundle(
    engine: Engine,
    repo_port: Any,
    *,
    bundle_root: Path,
    department_code: str = "",
    engagement_slug: str = "",
    customer_code: str = "",
    target_type: str = "",
    target_id: str = "",
    agent_key: str = "",
    username: str = "",
    sop_skills: str = "",
) -> AgentBundle:
    """`agent_key`：这次干活的是哪个 Agent；给它关联过的资产和项目关联的合在一起挑。

    `username`：只在私聊里给，带上这个人的个人必选资产。
    """
    request = {
        "department_code": department_code,
        "engagement_slug": engagement_slug,
        "customer_code": customer_code,
        "target_type": target_type,
        "target_id": target_id,
        "agent_key": agent_key,
        **({"username": username} if username else {}),
        **({"sop_skills": sop_skills} if sop_skills else {}),
    }
    chosen = _select(engine, request)
    heads = _heads(repo_port, chosen)
    files: dict[str, bytes] = {}
    carried: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(chosen.not_carried)
    rule_names: set[str] = set()
    for row in chosen.rows:
        if row.kind != "Rule" and row.kind not in BUNDLE_DIR:
            continue
        repo = RepoRef(name=row.repo)
        mount = chosen.mounted[row.asset_id]
        main_file = KIND_RULES[row.kind].main_file
        try:
            if row.kind == "Rule":
                mount = _rule_file_name(mount, rule_names)
                files[f"rules/{mount}.md"] = repo_port.read_file(repo, f"{row.path}/{main_file}")
            else:
                prefix = f"{BUNDLE_DIR[row.kind]}/{mount}"
                for entry in repo_port.list_tree(repo, row.path):
                    relative = entry.path[len(row.path) + 1 :]
                    content = repo_port.read_file(repo, entry.path)
                    if relative == "asset.yaml":
                        content = _platform_manifest(content, mount)
                    elif relative == main_file and mount != row.name:
                        content = _renamed(content, row.name, mount)
                    files[f"{prefix}/{relative}"] = content
        except FileNotFoundError:
            skipped.append({"asset": row.title, "asset_id": row.asset_id, "reason": "file_missing"})
            continue
        item = {
            "asset_id": row.asset_id,
            "kind": row.kind,
            "name": row.name,
            "mount_name": mount,
            "owner": row.owner_ref,
            "version": row.version,
            "commit_sha": row.commit_sha,
        }
        if row.kind == "Skill" and _COMMIT.match(row.commit_sha or ""):
            # 技能目录的完整文件清单：平台据此校验内容，并只给评审时允许的脚本加执行权限
            item["resource_manifest"] = _resource_manifest(
                files, f"{BUNDLE_DIR['Skill']}/{mount}", row
            )
        carried.append(item)

    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(files):
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(files[path]).digest())
    bundle = AgentBundle(revision=digest.hexdigest(), files=files, assets=carried, skipped=skipped)
    _store(Path(bundle_root), bundle)
    # 读文件期间仓库有新提交，读到的可能新旧混杂，这一份就不记，下次照常拼
    if _heads(repo_port, chosen) == heads:
        _remember(_selection_key(chosen, heads, request), bundle.revision)
    return bundle


_COMMIT = re.compile(r"^[0-9a-f]{40}$")


def _resource_manifest(files: dict[str, bytes], prefix: str, row: Any) -> dict[str, Any]:
    allowed = {
        str(path)
        for path in (json.loads(row.kind_spec_json or "{}").get("executables") or [])
        if isinstance(path, str)
    }
    listed = {}
    for path in sorted(files):
        if not path.startswith(prefix + "/"):
            continue
        relative = path[len(prefix) + 1 :]
        listed[relative] = {
            "sha256": hashlib.sha256(files[path]).hexdigest(),
            "bytes": len(files[path]),
            "executable": relative in allowed,
        }
    return {"version": 1, "source_revision": row.commit_sha, "files": listed}


def _store(root: Path, bundle: AgentBundle) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{bundle.revision}.json"
    if target.exists():
        return
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(bundle.as_payload(), ensure_ascii=False), encoding="utf-8")
    temporary.replace(target)


def load_bundle(bundle_root: Path, revision: str) -> AgentBundle:
    """按版本号取以前拼好的那一份。"""
    if not _REVISION.match(revision or ""):
        raise BundleNotFound(revision)
    target = Path(bundle_root) / f"{revision}.json"
    if not target.exists():
        raise BundleNotFound(revision)
    payload = json.loads(target.read_text(encoding="utf-8"))
    return AgentBundle(
        revision=payload["revision"],
        files={path: base64.b64decode(data) for path, data in payload["files"].items()},
        assets=payload.get("assets", []),
        skipped=payload.get("skipped", []),
    )


def resolve_path(bundle: AgentBundle, path: str) -> tuple[str, str] | None:
    """资产包里的一个文件路径是哪份资产的。返回（资产编号, 事件），认不出返回 None。

    事件：技能的主文件被读到算「加载」，其余都算「读取」。知识索引这类不属于任何
    一份资产的文件不计。
    """
    parts = [part for part in (path or "").split("/") if part]
    if len(parts) < 2:
        return None
    if parts[0] == "rules":
        kind, mount = "Rule", parts[1].removesuffix(".md")
        relative = KIND_RULES["Rule"].main_file
    else:
        directory = next(
            (
                (kind, len(prefix.split("/")))
                for kind, prefix in BUNDLE_DIR.items()
                if path.startswith(prefix + "/")
            ),
            None,
        )
        if directory is None or len(parts) <= directory[1]:
            return None
        kind, depth = directory
        mount, relative = parts[depth], "/".join(parts[depth + 1 :])
    for item in bundle.assets:
        if item.get("kind") == kind and item.get("mount_name") == mount:
            loaded = kind == "Skill" and relative == KIND_RULES["Skill"].main_file
            return item["asset_id"], "loaded" if loaded else "read"
    return None


#: Agent 写引用时用的前缀 → 资产类型。它写得不统一：有照类型写的（skill/x、Solution/x），
#: 有照目录写的（rules/x、knowledge/cases/x），有照知识索引写的（company:x），
#: 也有写中文的（方案/x、规范/x.md）。2026-10-17 按真实回复里的写法补齐
_REF_KIND_HINTS = {
    "rule": "Rule",
    "rules": "Rule",
    "规范": "Rule",
    "规则": "Rule",
    "skill": "Skill",
    "skills": "Skill",
    "技能": "Skill",
    "case": "Case",
    "cases": "Case",
    "案例": "Case",
    "问题": "Case",
    "sop": "Sop",
    "sops": "Sop",
    "流程": "Sop",
    "solution": "Solution",
    "solutions": "Solution",
    "方案": "Solution",
    "impl": "Implementation",
    "implementation": "Implementation",
    "implementations": "Implementation",
    "实施": "Implementation",
    "exp": "Experience",
    "experience": "Experience",
    "experiences": "Experience",
    "经验": "Experience",
}
#: 写成文件路径时，最后一段是这些文件名，资产名在前一段（knowledge/sops/x/SOP.md）
_MAIN_FILES = {"readme", "sop", "skill", "rule", "asset.yaml", "asset"}
_REF_SUFFIXES = (".md", ".yaml")


def resolve_ref(bundle: AgentBundle, ref: str, titles: dict[str, str] | None = None) -> str | None:
    """Agent 写的一处引用，是资产包里的哪份资产。认不出返回 None。

    按名字去对资产包里的挂载名；对不上再按标题对（`titles`：资产编号 → 标题）。
    同名不同类型时看前缀像哪一类。知识索引（INDEX）、写法示例（类型/名称）这类认不出，照常忽略。
    """
    pieces = [
        piece.strip()
        for piece in re.split(r"[/:：]", (ref or "").strip().strip("[]"))
        if piece.strip()
    ]
    if not pieces:
        return None
    name = pieces[-1]
    for suffix in _REF_SUFFIXES:
        name = name.removesuffix(suffix)
    if name.lower() in _MAIN_FILES and len(pieces) > 1:
        pieces = pieces[:-1]
        name = pieces[-1]
    found = [item for item in bundle.assets if item.get("mount_name") == name]
    if not found:
        # 也有人写不带负责人后缀的资产标识，或直接写标题
        found = [item for item in bundle.assets if item.get("name") == name]
    if not found and titles:
        found = [item for item in bundle.assets if titles.get(item.get("asset_id", "")) == name]
    if not found:
        return None
    hinted = {_REF_KIND_HINTS.get(piece.lower()) for piece in pieces[:-1]}
    preferred = [item for item in found if item.get("kind") in hinted]
    return (preferred or found)[0]["asset_id"]
