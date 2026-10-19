"""附件存储和数据库切换：草稿原件与入库附件同一套存储、附件下载、SQLite 搬到 PostgreSQL。"""

from __future__ import annotations

from pathlib import Path


from fde_asset.core.db import clean_text
from fde_asset.platform.blobs import BlobStore
from fde_asset.platform.files import FileStore
from tests.conftest import as_user


def test_draft_originals_live_in_the_shared_file_store(tmp_path: Path) -> None:
    files = FileStore(tmp_path / "files")
    drafts = BlobStore(tmp_path / "blobs", files=files)

    drafts.put("c1", "方案.docx", b"same bytes")
    drafts.put("c2", "another.docx", b"same bytes")
    sha256 = drafts.sha256("c1", "方案.docx")
    assert sha256 and sha256 == drafts.sha256("c2", "another.docx"), "同样的内容只存一份"
    assert files.get(sha256) == b"same bytes"
    assert drafts.get("c1", "方案.docx") == b"same bytes"
    assert drafts.names("c1") == ["方案.docx"]
    assert drafts.path("c1", "方案.docx") == files.path(sha256)

    assert drafts.delete("c1", "方案.docx") is True
    assert drafts.get("c1", "方案.docx") is None and drafts.names("c1") == []
    assert drafts.get("c2", "another.docx") == b"same bytes", "别的草稿引用着同一份内容，不能跟着没"


def test_originals_saved_before_the_switch_are_moved_on_first_read(tmp_path: Path) -> None:
    BlobStore(tmp_path / "blobs").put("c1", "old.pdf", b"legacy")  # 以前：内容直接放在草稿目录下
    files = FileStore(tmp_path / "files")
    drafts = BlobStore(tmp_path / "blobs", files=files)

    assert drafts.names("c1") == ["old.pdf"]
    assert drafts.get("c1", "old.pdf") == b"legacy"
    assert files.get(drafts.sha256("c1", "old.pdf")) == b"legacy"
    assert not (tmp_path / "blobs" / "c1" / "old.pdf").exists(), "挪进文件存储后原处不再留一份"
    assert drafts.names("c1") == ["old.pdf"]


def test_landed_attachment_can_be_downloaded_by_those_who_see_the_asset(client) -> None:
    chen = as_user(client, "chen")
    items = chen.get("/api/v1/assets", params={"limit": 200}).json()["items"]
    asset = next(item for item in items if item["name"] == "oracle-to-pg-cutover")
    attachment = next(a for a in asset["attachments"] if a["name"].endswith(".xlsx"))
    url = f"/api/v1/assets/{asset['asset_id']}/attachments/{attachment['name']}"

    got = chen.get(url)
    assert got.status_code == 200 and len(got.content) == attachment["bytes"]
    assert got.headers["content-disposition"].startswith("attachment")
    assert chen.get(url + ".nope").status_code == 404

    project = next(item for item in items if item["scope"] == "engagement")
    hidden = as_user(client, "zhao").get(
        f"/api/v1/assets/{project['asset_id']}/attachments/whatever.pdf"
    )
    assert hidden.status_code == 404, "看不到的资产，附件也拿不到"


def test_null_characters_are_dropped_before_storing() -> None:
    assert clean_text("a\x00b") == "ab"
    assert clean_text("plain") == "plain"


def test_old_sqlite_data_is_copied_into_an_empty_postgres(tmp_path: Path, settings) -> None:
    """生产目标机升级用：旧版本把数据存在数据目录的 asset.db（SQLite），新版本第一次起来时搬进 PostgreSQL。

    资产中心 2026-10-18 起只用 PostgreSQL，这里直接按表结构造一个旧版的 SQLite 文件。
    """
    from sqlalchemy import create_engine, func, select

    from fde_asset.api.deps import build_context
    from fde_asset.core.db import (
        asset_events,
        assets,
        create_engine_for,
        init_db,
        metadata,
        record_event,
    )

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    old = create_engine(f"sqlite+pysqlite:///{settings.db_path}", future=True)
    metadata.create_all(old)
    with old.begin() as conn:
        for index in range(3):
            conn.execute(
                assets.insert().values(
                    asset_id=f"old-{index}",
                    scope="company",
                    repo="assets",
                    path=f"assets/old-{index}",
                    kind="Case",
                    name=f"old-{index}",
                    title=f"旧库里的资产 {index}",
                    owner_ref="user:chen",
                )
            )
        record_event(conn, "test.marker", {"text": "带\x00空字符"})
        before_assets = conn.execute(select(func.count()).select_from(assets)).scalar_one()
        before_events = conn.execute(select(func.count()).select_from(asset_events)).scalar_one()
    old.dispose()

    # 换成 PostgreSQL 启动：空库 + 数据目录里留着 SQLite 文件 → 自动搬
    context = build_context(settings)
    with context.engine.begin() as conn:
        assert conn.execute(select(func.count()).select_from(assets)).scalar_one() == before_assets
        assert (
            conn.execute(select(func.count()).select_from(asset_events)).scalar_one()
            >= before_events
        )
        # 自增编号接着往后排，不会和搬过来的撞上
        record_event(conn, "test.after", {})
        stamp = conn.execute(select(assets.c.created_at).limit(1)).scalar_one()
        assert stamp.tzinfo is not None
    assert not settings.db_path.exists(), "搬完后 SQLite 文件改名留档"
    assert settings.db_path.with_name(settings.db_path.name + ".migrated-to-postgres").exists()

    # 再启动一次不会重复搬
    again = create_engine_for(settings.database_target())
    init_db(again)
    with again.connect() as conn:
        assert conn.execute(select(func.count()).select_from(assets)).scalar_one() == before_assets
