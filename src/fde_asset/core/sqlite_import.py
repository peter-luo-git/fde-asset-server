"""从 SQLite 换到 PostgreSQL 时，把原来的数据搬过来。

只在 PostgreSQL 还是空库、而数据目录里留着 SQLite 文件时做一次；搬完把那个文件改名留档，
之后再启动就不会重复搬。表结构以代码里的定义为准，两边都有的列才搬。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, String, create_engine, func, inspect, select
from sqlalchemy.engine import Engine

from fde_asset.core.db import metadata

logger = logging.getLogger(__name__)

#: 搬完之后 SQLite 文件改成这个后缀留档
DONE_SUFFIX = ".migrated-to-postgres"


def _fit(table, row: dict) -> dict:
    """把 SQLite 里的一行整理成 PostgreSQL 收得下的样子。"""
    fitted = {}
    for column in table.columns:
        if column.name not in row:
            continue
        value = row[column.name]
        if isinstance(value, str):
            # SQLite 不管长度也不管空字符，PostgreSQL 都管
            value = value.replace("\x00", "")
            limit = getattr(column.type, "length", None)
            if isinstance(column.type, String) and limit and len(value) > limit:
                logger.warning(
                    "%s.%s 有一条超过 %d 个字符，搬的时候截短了", table.name, column.name, limit
                )
                value = value[:limit]
        elif isinstance(value, datetime) and isinstance(column.type, DateTime):
            # SQLite 里存的时间不带时区，实际都是 UTC
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
        fitted[column.name] = value
    return fitted


def import_sqlite_if_empty(engine: Engine, sqlite_path: Path) -> tuple[int, int] | None:
    """返回（搬了几张表, 共几行）；不需要搬就返回 None。"""
    if engine.dialect.name != "postgresql":
        return None
    source_path = Path(sqlite_path).expanduser()
    if not source_path.exists():
        return None
    with engine.connect() as conn:
        for table in metadata.sorted_tables:
            if conn.execute(select(func.count()).select_from(table)).scalar_one():
                return None  # 已经有数据了，不是第一次

    source = create_engine(f"sqlite+pysqlite:///{source_path}", future=True)
    existing = set(inspect(source).get_table_names())
    tables = rows_total = 0
    try:
        with source.connect() as reader, engine.begin() as writer:
            for table in metadata.sorted_tables:
                if table.name not in existing:
                    continue
                present = {item["name"] for item in inspect(source).get_columns(table.name)}
                columns = [column for column in table.columns if column.name in present]
                rows = [_fit(table, dict(row._mapping)) for row in reader.execute(select(*columns))]
                if rows:
                    writer.execute(table.insert(), rows)
                    tables += 1
                    rows_total += len(rows)
                # 自增的编号是原样搬过来的，序列要拨到最大值之后，否则下一次插入会撞上
                if "id" in table.c and table.c.id.autoincrement is True:
                    writer.exec_driver_sql(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
                    )
    finally:
        source.dispose()
    source_path.rename(source_path.with_name(source_path.name + DONE_SUFFIX))
    return tables, rows_total
