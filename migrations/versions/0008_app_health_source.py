"""探活结果记下是谁探的：平台自己，还是某个用户的电脑替它探的。

Revision ID: 0008_app_health_source
Revises: 0007_candidate_customer
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_app_health_source"
down_revision: Union[str, None] = "0007_candidate_customer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "app_health",
        sa.Column("checked_via", sa.String(16), nullable=False, server_default="server"),
    )
    op.add_column(
        "app_health", sa.Column("checked_by", sa.String(64), nullable=False, server_default="")
    )


def downgrade() -> None:
    op.drop_column("app_health", "checked_by")
    op.drop_column("app_health", "checked_via")
