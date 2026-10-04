"""旧库升级：代码加了列，沿用的旧数据目录不该一启动就报 no such column。"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Column, String, inspect, select

from fde_asset.core.db import add_missing_columns, assets, create_engine_for, init_db, metadata


def _old_db(tmp_path: Path):
    """造一个缺 customer_code 列、且已有一行数据的旧库。"""
    engine = create_engine_for(tmp_path / "asset.db")
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            assets.insert().values(
                asset_id="a1",
                scope="company",
                repo="company-assets",
                path="knowledge/cases/a1",
                kind="Case",
                name="a1",
                title="旧库里的资产",
                owner_ref="user:chen",
                version="0.1.0",
                commit_sha="c0ffee",
            )
        )
        conn.exec_driver_sql("ALTER TABLE assets DROP COLUMN customer_code")
    return engine


def _columns(engine) -> set[str]:
    return {item["name"] for item in inspect(engine).get_columns("assets")}


def test_init_db_adds_missing_column_and_keeps_rows(tmp_path: Path) -> None:
    engine = _old_db(tmp_path)
    assert "customer_code" not in _columns(engine)

    init_db(engine)

    assert "customer_code" in _columns(engine)
    with engine.connect() as conn:
        row = conn.execute(select(assets.c.asset_id, assets.c.customer_code)).one()
    assert row.asset_id == "a1", "原有数据要留着"
    assert row.customer_code == "", "非空列按代码里的默认值回填"
    assert add_missing_columns(engine) == [], "补过一次就不该再动"


def test_alembic_managed_db_is_left_alone(tmp_path: Path) -> None:
    engine = _old_db(tmp_path)
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE alembic_version (version_num VARCHAR(32))")

    assert add_missing_columns(engine) == []
    assert "customer_code" not in _columns(engine), "迁移脚本管的库交给迁移脚本"


def test_not_null_column_without_default_is_refused(tmp_path: Path) -> None:
    engine = _old_db(tmp_path)
    column = Column("no_default", String(8), nullable=False)
    assets.append_column(column)
    try:
        with pytest.raises(RuntimeError, match="assets.no_default"):
            add_missing_columns(engine)
    finally:
        assets._columns.remove(column)
