"""沉淀候选：会话里可能值得沉淀成资产的内容，统一记下来，点「沉淀资产」时再处理。

Revision ID: 0016_harvest_suggestions
Revises: 0015_attachment_texts
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_harvest_suggestions"
down_revision: Union[str, None] = "0015_attachment_texts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "harvest_suggestions" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "harvest_suggestions",
            sa.Column("id", sa.String(32), primary_key=True),
            sa.Column("engagement_slug", sa.String(128), nullable=False),
            sa.Column("scope", sa.String(16), nullable=False),
            sa.Column("scope_id", sa.String(64), nullable=False),
            sa.Column("conversation_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("owner_user", sa.String(128), nullable=False, server_default=""),
            sa.Column("source", sa.String(16), nullable=False),
            sa.Column("kind", sa.String(32), nullable=False, server_default=""),
            sa.Column("title", sa.String(255), nullable=False, server_default=""),
            sa.Column("summary", sa.Text, nullable=False, server_default=""),
            sa.Column("message_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("files_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("extra_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("status", sa.String(16), nullable=False, server_default="open"),
            sa.Column("candidate_id", sa.String(32), nullable=False, server_default=""),
            sa.Column("created_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("decided_by", sa.String(128), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )
        op.create_index(
            "ix_harvest_suggestions_scope", "harvest_suggestions", ["scope", "scope_id", "status"]
        )


def downgrade() -> None:
    op.drop_table("harvest_suggestions")
