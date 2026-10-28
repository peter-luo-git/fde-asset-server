"""沉淀候选的处理方式：待定、已有资产、重复、不沉淀的原因，以及处理后又被提到的记录。

Revision ID: 0017_harvest_suggestion_decisions
Revises: 0016_harvest_suggestions
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_harvest_suggestion_decisions"
down_revision: Union[str, None] = "0016_harvest_suggestions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

COLUMNS = (
    sa.Column("decision_note", sa.Text(), nullable=False, server_default=""),
    sa.Column("related_asset_id", sa.String(64), nullable=False, server_default=""),
    sa.Column("merged_into", sa.String(32), nullable=False, server_default=""),
    sa.Column("mentions", sa.Integer(), nullable=False, server_default="0"),
    sa.Column("mentions_json", sa.Text(), nullable=False, server_default="[]"),
    sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
)


def upgrade() -> None:
    have = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("harvest_suggestions")
    }
    for column in COLUMNS:
        if column.name not in have:
            op.add_column("harvest_suggestions", column.copy())
    op.create_index(
        "ix_harvest_suggestions_project_status",
        "harvest_suggestions",
        ["engagement_slug", "status"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index("ix_harvest_suggestions_project_status", "harvest_suggestions", if_exists=True)
    for column in reversed(COLUMNS):
        op.drop_column("harvest_suggestions", column.name)
