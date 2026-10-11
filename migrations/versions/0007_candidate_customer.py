"""草稿表增加 customer_code，客户级草稿才能提交到对应的客户仓库。

Revision ID: 0007_candidate_customer
Revises: 0006_customer_scope
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007_candidate_customer"
down_revision: Union[str, None] = "0006_customer_scope"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    """0001 是按当时的代码建全部表的；在新库上，后来才加的列它已经一并建好了，这里就不能再加一次。"""
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("harvest_candidates", "customer_code"):
        op.add_column(
            "harvest_candidates",
            sa.Column("customer_code", sa.String(64), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("harvest_candidates", "customer_code")
