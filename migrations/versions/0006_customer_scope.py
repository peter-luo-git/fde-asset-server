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


def _has_column(table: str, column: str) -> bool:
    """0001 是按当时的代码建全部表的；在新库上，后来才加的列它已经一并建好了，这里就不能再加一次。"""
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("assets", "customer_code"):
        op.add_column(
            "assets",
            sa.Column("customer_code", sa.String(64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("assets", "customer_code")
