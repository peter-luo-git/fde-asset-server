"""资产表增加 customer_code，支持客户级作用域。

Revision ID: 0006_customer_scope
Revises: 0005_subscriptions
Create Date: 2026-10-02
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_customer_scope"
down_revision: Union[str, None] = "0005_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "assets",
        sa.Column("customer_code", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("assets", "customer_code")
