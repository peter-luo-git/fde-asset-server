"""应用资产的探活结果表。

Revision ID: 0003_app_health
Revises: 0002_recommendations
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from fde_asset.core.db import app_health

revision: str = "0003_app_health"
down_revision: Union[str, None] = "0002_recommendations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    app_health.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    app_health.drop(op.get_bind(), checkfirst=True)
