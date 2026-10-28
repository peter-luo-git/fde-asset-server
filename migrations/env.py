"""Alembic 环境：资产服务独立迁移链，不与 fde-server 共用。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import String, engine_from_config, inspect, pool

config = context.config
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _url() -> str:
    """连哪个库：和服务本身用同一套配置（FDE_ASSET_DATABASE_URL，只能是 PostgreSQL）。"""
    from sqlalchemy.engine import make_url

    from fde_asset.settings import AssetSettings

    target = AssetSettings().database_target()
    return (
        make_url(target).set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
    )


def run_migrations_online() -> None:
    url = _url()
    connectable = engine_from_config(
        {"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            if connection.dialect.name == "postgresql":
                migration = context.get_context()
                # Alembic 1.14.1 默认 VARCHAR(32)，上游 0017 的历史 revision 为33字符。
                # 用官方工厂保留版本表约束；空库建64列，旧库仅在过短时扩列。
                version = migration.impl.version_table_impl(
                    version_table=migration.version_table,
                    version_table_schema=migration.version_table_schema,
                    version_table_pk=True,
                )
                version.c.version_num.type = String(64)
                version.create(connection, checkfirst=True)
                column = next(
                    item
                    for item in inspect(connection).get_columns(
                        migration.version_table, schema=migration.version_table_schema
                    )
                    if item["name"] == "version_num"
                )
                width = getattr(column["type"], "length", None)
                if width is not None and width < 64:
                    table_name = connection.dialect.identifier_preparer.format_table(version)
                    connection.exec_driver_sql(
                        f"ALTER TABLE {table_name} ALTER COLUMN version_num TYPE VARCHAR(64)"
                    )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
