"""应用容器化演示的部署表。

Revision ID: 0004_app_deployments
Revises: 0003_app_health
Create Date: 2026-10-01
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from fde_asset.core.db import app_deployments

revision: str = "0004_app_deployments"
down_revision: Union[str, None] = "0003_app_health"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    app_deployments.create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    app_deployments.drop(op.get_bind(), checkfirst=True)
