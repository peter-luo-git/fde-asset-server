"""订阅、站内通知、资产版本三张表。

Revision ID: 0005_subscriptions
Revises: 0004_app_deployments
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

from fde_asset.core.db import asset_notifications, asset_subscriptions, asset_versions

revision: str = "0005_subscriptions"
down_revision: Union[str, None] = "0004_app_deployments"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = (asset_subscriptions, asset_notifications, asset_versions)


def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
