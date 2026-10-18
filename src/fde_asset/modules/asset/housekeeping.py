"""定时清理（B6，2026-10-18）：资产包存档和没人引用的附件原件，原来只增不减。

- **资产包存档**（``bundles/<版本号>.json``）：平台重试一次执行时按版本号来取同一份，使用上报也按
  版本号反查是哪份资产，所以不能随便删。保留最近 ``BUNDLE_KEEP_DAYS`` 天的，外加最新的
  ``BUNDLE_KEEP_LATEST`` 份；其余删掉。
- **附件原件**（按内容哈希存放的文件存储）：标记清扫。资产表里任何一行（包括已删除的）
  的附件清单、任何草稿记下的原件引用，都算有人引用；其余的超过 ``FILE_GRACE_DAYS`` 天才删，
  刚上传、还没来得及登记的原件不会被误删。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from fde_asset.core.db import assets

logger = logging.getLogger(__name__)

BUNDLE_KEEP_DAYS = 30
BUNDLE_KEEP_LATEST = 20
FILE_GRACE_DAYS = 7
_DAY = 86400.0


def prune_bundles(
    bundle_root: Path,
    *,
    keep_days: float = BUNDLE_KEEP_DAYS,
    keep_latest: int = BUNDLE_KEEP_LATEST,
    now: float | None = None,
) -> int:
    root = Path(bundle_root)
    if not root.is_dir():
        return 0
    now = time.time() if now is None else now
    files = sorted(
        (path for path in root.glob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for path in files[keep_latest:]:
        if now - path.stat().st_mtime > keep_days * _DAY:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def referenced_files(engine: Engine, blob_root: Path) -> set[str]:
    """还有人引用的附件哈希：资产（包括已删除的）的附件清单，加上草稿记下的原件。"""
    found: set[str] = set()
    with engine.connect() as conn:
        for (raw,) in conn.execute(select(assets.c.attachments_json)):
            try:
                items = json.loads(raw or "[]")
            except ValueError:
                continue
            for item in items if isinstance(items, list) else []:
                if isinstance(item, dict) and isinstance(item.get("sha256"), str):
                    found.add(item["sha256"])
    root = Path(blob_root)
    if root.is_dir():
        for ref in root.glob("*/.refs/*"):
            if ref.is_file():
                found.add(ref.read_text(encoding="utf-8").strip())
    return found


def prune_files(
    engine: Engine,
    file_root: Path,
    blob_root: Path,
    *,
    grace_days: float = FILE_GRACE_DAYS,
    now: float | None = None,
) -> int:
    root = Path(file_root)
    if not root.is_dir():
        return 0
    now = time.time() if now is None else now
    keep = referenced_files(engine, blob_root)
    removed = 0
    for path in root.glob("??/*"):
        if not path.is_file() or path.name.startswith(".") or path.name in keep:
            continue
        if now - path.stat().st_mtime > grace_days * _DAY:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def run(context: Any) -> dict[str, int]:
    settings = context.settings
    result = {
        "bundles": prune_bundles(settings.bundle_dir),
        "files": prune_files(context.engine, settings.file_dir, settings.blob_dir),
    }
    if any(result.values()):
        logger.warning(
            "定时清理：资产包存档 %d 份、没人引用的附件 %d 个", result["bundles"], result["files"]
        )
    return result
