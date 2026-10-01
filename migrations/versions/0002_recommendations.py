"""推荐、关联、反馈与系统配置四张表。

Revision ID: 0002_recommendations
Revises: 0001_asset_core
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from fde_asset.core.db import (
    asset_feedback,
    asset_recommendations,
    system_settings,
    target_assets,
)

revision: str = "0002_recommendations"
down_revision: Union[str, None] = "0001_asset_core"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (asset_recommendations, target_assets, asset_feedback, system_settings)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
