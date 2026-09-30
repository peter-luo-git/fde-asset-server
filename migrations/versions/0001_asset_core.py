"""资产中心初始表结构。

v0.1 直接由 `fde_asset.core.db.metadata` 建表，保证模型与迁移不会分叉；
后续变更改为显式 op.* 语句，并保留升级与回滚验证。

Revision ID: 0001_asset_core
Revises:
Create Date: 2026-09-30
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from fde_asset.core.db import metadata

revision: str = "0001_asset_core"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    metadata.create_all(op.get_bind())


def downgrade() -> None:
    metadata.drop_all(op.get_bind())
