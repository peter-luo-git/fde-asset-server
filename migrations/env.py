"""Alembic 环境：资产服务独立迁移链，不与 fde-server 共用。"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def _url() -> str:
    """连哪个库：和服务本身用同一套配置（环境变量），没配才用 alembic.ini 里那个本地 SQLite。"""
    import os

    if os.environ.get("FDE_ASSET_DATABASE_URL") or os.environ.get("FDE_ASSET_DATA_DIR"):
        from sqlalchemy.engine import make_url

        from fde_asset.settings import AssetSettings

        target = AssetSettings().database_target()
        if str(target).startswith("postgresql"):
            return (
                make_url(str(target))
                .set(drivername="postgresql+psycopg")
                .render_as_string(hide_password=False)
            )
        return f"sqlite:///{target}"
    return config.get_main_option("sqlalchemy.url").replace("+aiosqlite", "")


def run_migrations_online() -> None:
    url = _url()
    connectable = engine_from_config(
        {"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
