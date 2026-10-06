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


def upgrade() -> None:
    op.add_column(
        "harvest_candidates",
        sa.Column("customer_code", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("harvest_candidates", "customer_code")
