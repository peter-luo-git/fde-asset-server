"""给平台的 Agent 资产包：某个项目的 Agent 该带的资产，拼成一份文件清单。

平台在 Agent 启动前来要这份清单，自己校验后整理成只读目录挂进运行容器。
资产在仓库里按编号放、分属不同的可见范围；这里按「这个项目看得见、关联了哪些」挑出来，
摆成平台认的目录形状：

    rules/<两位序号>-<名字>.md
    skills/<名字>/SKILL.md、asset.yaml 和技能自带的其他文件
    knowledge/cases/<名字>/README.md、asset.yaml

名字是挂载名：平时就是资产的标识，同一个项目里撞名的那份加了负责人。
平台现在只认规范、技能、问题案例这三类，其余类型不放进包里。

每份包按内容算出一个版本号并存档。平台记下一次运行用的是哪个版本，重试时按版本号
来取同一份，不会因为中途有新资产入库而变样。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.engine import Engine

from fde_asset.modules.asset.manifest import KIND_RULES
from fde_asset.modules.asset.snapshot import _renamed, select_assets
from fde_asset.platform.repo.ports import RepoRef

#: 平台认的类型 → 包里的目录
BUNDLE_DIR = {"Skill": "skills", "Case": "knowledge/cases"}
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


class BundleNotFound(LookupError):
    pass


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
) -> AgentBundle:
    chosen = select_assets(
        engine,
        department_code=department_code,
        engagement_slug=engagement_slug,
        customer_code=customer_code,
        target_type=target_type,
        target_id=target_id,
    )
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
        carried.append(
            {
                "asset_id": row.asset_id,
                "kind": row.kind,
                "name": row.name,
                "mount_name": mount,
                "owner": row.owner_ref,
                "version": row.version,
                "commit_sha": row.commit_sha,
            }
        )

    digest = hashlib.sha1(usedforsecurity=False)
    for path in sorted(files):
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(files[path]).digest())
    bundle = AgentBundle(revision=digest.hexdigest(), files=files, assets=carried, skipped=skipped)
    _store(Path(bundle_root), bundle)
    return bundle


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
