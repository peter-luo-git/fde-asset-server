"""定时体检的结果；可见范围里点名的人和资产的关系（人员变动提醒）。

Revision ID: 0011_checkup_findings
Revises: 0010_visibility_requests
Create Date: 2026-10-18
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_checkup_findings"
down_revision: Union[str, None] = "0010_visibility_requests"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "asset_checkup_findings" not in tables:
        op.create_table(
            "asset_checkup_findings",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("asset_id", sa.String(64), nullable=False),
            sa.Column("rule", sa.String(32), nullable=False),
            sa.Column("subject", sa.String(200), nullable=False, server_default=""),
            sa.Column("owner_ref", sa.String(128), nullable=False, server_default=""),
            sa.Column("detail", sa.Text, nullable=False, server_default=""),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("resolved_at", sa.DateTime(timezone=True)),
            sa.Column("notified_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("asset_id", "rule", "subject", name="uq_checkup_finding"),
        )
    if "asset_named_audience" not in tables:
        op.create_table(
            "asset_named_audience",
            sa.Column("asset_id", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.String(64), primary_key=True),
            sa.Column("related", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("asset_named_audience")
    op.drop_table("asset_checkup_findings")
