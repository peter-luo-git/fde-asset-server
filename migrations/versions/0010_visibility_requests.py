"""可见范围申请；草稿记下修订的是哪份资产、起草时想放的额外可见范围。

Revision ID: 0010_visibility_requests
Revises: 0009_asset_mandatory
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_visibility_requests"
down_revision: Union[str, None] = "0009_asset_mandatory"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    # 0001 按当时的代码建全部表，新库上这些表和列已经建好了
    columns = {item["name"] for item in _inspector().get_columns("harvest_candidates")}
    if "revises_asset_id" not in columns:
        op.add_column(
            "harvest_candidates",
            sa.Column("revises_asset_id", sa.String(64), nullable=False, server_default=""),
        )
    if "visibility_json" not in columns:
        op.add_column(
            "harvest_candidates",
            sa.Column("visibility_json", sa.Text, nullable=False, server_default="[]"),
        )
    tables = set(_inspector().get_table_names())
    if "visibility_requests" not in tables:
        op.create_table(
            "visibility_requests",
            sa.Column("request_id", sa.String(64), primary_key=True),
            sa.Column("asset_id", sa.String(64), nullable=False),
            sa.Column("entry", sa.String(80), nullable=False),
            sa.Column("requested_by", sa.String(64), nullable=False),
            sa.Column("note", sa.Text, nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("decided_by", sa.String(64), nullable=False, server_default=""),
            sa.Column("decision_note", sa.Text, nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True)),
        )
        op.create_index(
            "ix_visibility_requests_asset", "visibility_requests", ["asset_id", "status"]
        )


def downgrade() -> None:
    op.drop_index("ix_visibility_requests_asset", table_name="visibility_requests")
    op.drop_table("visibility_requests")
    op.drop_column("harvest_candidates", "visibility_json")
    op.drop_column("harvest_candidates", "revises_asset_id")
