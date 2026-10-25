"""公开技能库：管理员配来源（Git 仓库），资产中心浅克隆下来列出里面的技能，人挑了装进草稿。

- 只认 https 地址，只从管理员配过的来源装；装的时候固定到当时的提交号，记下来源、目录、版本、许可证；
- 网络不通时页面给出"手动下载再上传导入"的做法（`manual_steps`）；
- 装进来的是草稿：照样过开头信息检查、安全检查、试跑，而且不管归属哪一级都要公司资产评审员评；
- 来源里这个技能的内容变了（按目录内容摘要比），资产详情页提示有新版本，点更新生成修订草稿，同样要评审。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets, skill_sources
from fde_asset.modules.asset import skill_package

DEFAULT_SOURCES = (
    {"name": "Anthropic 官方技能库", "url": "https://github.com/anthropics/skills", "branch": ""},
)
URL_PATTERN = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._~/-]+$")
GIT_TIMEOUT = 90
MAX_DEPTH = 5
#: 同一个来源两次刷新至少隔这么久，免得有人反复点把外网拖慢
REFRESH_INTERVAL_SECONDS = 60


class SkillSourceError(ValueError):
    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_defaults(engine: Engine) -> None:
    with engine.begin() as conn:
        if conn.execute(select(skill_sources.c.id).limit(1)).first() is None:
            for source in DEFAULT_SOURCES:
                conn.execute(skill_sources.insert().values(**source, created_by="seed"))


def listing(engine: Engine) -> list[dict[str, Any]]:
    ensure_defaults(engine)
    with engine.connect() as conn:
        rows = conn.execute(select(skill_sources).order_by(skill_sources.c.id)).fetchall()
    return [_view(row) for row in rows]


def _view(row: Any) -> dict[str, Any]:
    catalog = json.loads(row.catalog_json or "[]")
    return {
        "id": row.id,
        "name": row.name,
        "url": row.url,
        "branch": row.branch,
        "enabled": row.enabled,
        "status": row.status or "never",
        "error": row.error,
        "commit_sha": row.commit_sha,
        "skills": len(catalog),
        "checked_at": row.checked_at.isoformat() if row.checked_at else "",
    }


def add(engine: Engine, *, name: str, url: str, branch: str, actor: str) -> dict[str, Any]:
    url = url.strip().rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    if not URL_PATTERN.match(url):
        raise SkillSourceError("来源地址只能是 https 的 Git 仓库地址")
    if branch and not re.match(r"^[A-Za-z0-9._/-]{1,100}$", branch):
        raise SkillSourceError("分支名不对")
    with engine.begin() as conn:
        if conn.execute(select(skill_sources.c.id).where(skill_sources.c.url == url)).first():
            raise SkillSourceError("这个来源已经有了")
        conn.execute(
            skill_sources.insert().values(
                name=(name or url)[:100], url=url, branch=branch, created_by=actor
            )
        )
        row = conn.execute(select(skill_sources).where(skill_sources.c.url == url)).first()
    return _view(row)


def remove(engine: Engine, source_id: int, cache_root: Path) -> bool:
    with engine.begin() as conn:
        removed = conn.execute(
            delete(skill_sources).where(skill_sources.c.id == source_id)
        ).rowcount
    shutil.rmtree(cache_root / str(source_id), ignore_errors=True)
    return bool(removed)


def _source(engine: Engine, source_id: int) -> Any:
    with engine.connect() as conn:
        row = conn.execute(select(skill_sources).where(skill_sources.c.id == source_id)).first()
    if row is None:
        raise SkillSourceError("没有这个来源", status=404)
    return row


def _git(args: list[str], cwd: Path | None = None) -> str:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_TERMINAL_PROMPT": "0",
        # 只走 https：不跟着仓库里的设置去连 file://、ssh 之类
        "GIT_ALLOW_PROTOCOL": "https",
        **{k: v for k, v in os.environ.items() if k.lower() in {"https_proxy", "no_proxy"}},
    }
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT,
        check=False,
    )
    if result.returncode != 0:
        raise SkillSourceError(
            (result.stderr or result.stdout or "git 失败").strip()[-400:], status=502
        )
    return result.stdout.strip()


def manual_steps(url: str, branch: str = "", path: str = "", commit: str = "") -> list[str]:
    """网络不通时页面上给的操作说明。"""
    target = path or "<技能目录>"
    return [
        f"在能上网的电脑上下载：git clone {url}" + (f" -b {branch}" if branch else ""),
        f"进入仓库，切到要装的版本：git checkout {commit}"
        if commit
        else "进入仓库，记下当前版本号：git rev-parse HEAD",
        f"把技能目录 {target} 打成 zip（zip 里最外层是 SKILL.md，或者只包一层目录）",
        "回到资产中心：技能市场 →「手动导入」，上传这个 zip，并填上来源地址、技能目录和版本号",
        "导入后和直接安装一样：先过安全检查和试跑，再由公司资产评审员评",
    ]


def refresh(
    engine: Engine, source_id: int, cache_root: Path, *, force: bool = False
) -> dict[str, Any]:
    """浅克隆（或更新）来源，列出里面的技能。网络不通记成 unreachable，不抛给页面。"""
    row = _source(engine, source_id)
    if (
        not force
        and row.checked_at
        and (
            _now() - row.checked_at.replace(tzinfo=row.checked_at.tzinfo or timezone.utc)
        ).total_seconds()
        < REFRESH_INTERVAL_SECONDS
        and row.status == "ok"
    ):
        return _view(row)
    target = cache_root / str(source_id)
    try:
        if (target / ".git").is_dir():
            _git(["fetch", "--depth", "1", "origin", row.branch or "HEAD"], cwd=target)
            _git(["reset", "--hard", "FETCH_HEAD"], cwd=target)
        else:
            shutil.rmtree(target, ignore_errors=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            _git(
                [
                    "clone",
                    "--depth",
                    "1",
                    *(["-b", row.branch] if row.branch else []),
                    row.url,
                    str(target),
                ]
            )
        commit = _git(["rev-parse", "HEAD"], cwd=target)
        catalog = _scan_catalog(target)
        values = {
            "status": "ok",
            "error": "",
            "commit_sha": commit,
            "catalog_json": json.dumps(catalog, ensure_ascii=False),
        }
    except (SkillSourceError, subprocess.TimeoutExpired, OSError) as exc:
        values = {"status": "unreachable", "error": str(exc)[-400:] or "连不上"}
    with engine.begin() as conn:
        conn.execute(
            update(skill_sources)
            .where(skill_sources.c.id == source_id)
            .values(checked_at=_now(), **values)
        )
    return _view(_source(engine, source_id))


def _dir_files(directory: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(directory.rglob("*")):
        relative = path.relative_to(directory).as_posix()
        if ".git" in relative.split("/") or path.is_dir():
            continue
        if path.is_symlink():
            files[relative] = b""  # 由 normalize 按符号链接拒收
            continue
        if path.stat().st_size > skill_package.MAX_FILE_BYTES:
            files[relative] = None  # type: ignore[assignment]
            continue
        files[relative] = path.read_bytes()
    return files


def _digest(files: dict[str, bytes]) -> str:
    content = hashlib.sha256()
    for path in sorted(files):
        content.update(path.encode() + b"\0" + hashlib.sha256(files[path] or b"").digest())
    return content.hexdigest()


def _license(directory: Path, root: Path) -> str:
    for base in (directory, root):
        for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "license.txt"):
            candidate = base / name
            if candidate.is_file():
                head = candidate.read_text(encoding="utf-8", errors="ignore")[:400]
                first = next((line.strip() for line in head.splitlines() if line.strip()), "")
                return first[:80] or name
    return ""


def _scan_catalog(root: Path) -> list[dict[str, Any]]:
    catalog = []
    for skill_md in sorted(root.rglob("SKILL.md")):
        directory = skill_md.parent
        relative = directory.relative_to(root).as_posix()
        if ".git" in relative.split("/") or relative.count("/") >= MAX_DEPTH:
            continue
        front = (
            skill_package.parse_frontmatter(skill_md.read_text(encoding="utf-8", errors="ignore"))
            or {}
        )
        files = _dir_files(directory)
        scripts = [
            path
            for path, data in files.items()
            if data and skill_package.is_script(path, data[:200].decode("utf-8", errors="ignore"))
        ]
        catalog.append(
            {
                "path": "" if relative == "." else relative,
                "name": str(front.get("name") or directory.name)[:64],
                "description": " ".join(str(front.get("description") or "").split())[:600],
                "files": len(files),
                "scripts": len(scripts),
                "license": _license(directory, root),
                "digest": _digest(files),
            }
        )
    return catalog[:500]


def catalog(engine: Engine, source_id: int, query: str = "") -> dict[str, Any]:
    row = _source(engine, source_id)
    entries = json.loads(row.catalog_json or "[]")
    if query:
        lowered = query.lower()
        entries = [e for e in entries if lowered in (e["name"] + " " + e["description"]).lower()]
    installed = installed_from(engine, row.url)
    for entry in entries:
        found = installed.get(entry["path"])
        entry["installed"] = (
            {
                "asset_id": found["asset_id"],
                "title": found["title"],
                "outdated": bool(found["digest"]) and found["digest"] != entry["digest"],
            }
            if found
            else None
        )
    return {
        "source": _view(row),
        "entries": entries,
        "manual_steps": manual_steps(row.url, row.branch, commit=row.commit_sha),
    }


def installed_from(engine: Engine, url: str) -> dict[str, dict[str, Any]]:
    """从这个来源装进来、已入库的技能：{来源里的目录: 资产}。"""
    found: dict[str, dict[str, Any]] = {}
    with engine.connect() as conn:
        rows = conn.execute(
            select(assets.c.asset_id, assets.c.title, assets.c.source_json).where(
                assets.c.kind == "Skill", assets.c.deleted_at.is_(None)
            )
        ).fetchall()
    for row in rows:
        source = json.loads(row.source_json or "{}")
        if source.get("url") == url:
            found[str(source.get("path") or "")] = {
                "asset_id": row.asset_id,
                "title": row.title,
                "digest": str(source.get("digest") or ""),
            }
    return found


def package_for(
    engine: Engine, source_id: int, path: str, cache_root: Path
) -> tuple[Any, dict[str, Any]]:
    """从来源的缓存里取出一个技能目录，整理成技能包；同时给出要记进资产的来源信息。"""
    row = _source(engine, source_id)
    entries = {e["path"]: e for e in json.loads(row.catalog_json or "[]")}
    entry = entries.get(path)
    if row.status != "ok" or entry is None:
        raise SkillSourceError("先刷新这个来源；网络不通的话按页面上的说明手动下载导入")
    root = (cache_root / str(source_id)).resolve()
    directory = (root / path).resolve() if path else root
    if not directory.is_relative_to(root) or not (directory / "SKILL.md").is_file():
        raise SkillSourceError("来源里没有这个技能目录了，先刷新")
    raw = _dir_files(directory)
    symlinks = {p for p, data in raw.items() if data == b"" and (directory / p).is_symlink()}
    package = skill_package.normalize(raw, symlinks=symlinks)
    source = {
        "origin": "install",
        "url": row.url,
        "path": path,
        "commit": row.commit_sha,
        "license": entry.get("license", ""),
        "digest": entry["digest"],
        "sourceName": row.name,
    }
    return package, source
