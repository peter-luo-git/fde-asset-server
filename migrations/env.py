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


def run_migrations_online() -> None:
    url = config.get_main_option("sqlalchemy.url").replace("+aiosqlite", "")
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
