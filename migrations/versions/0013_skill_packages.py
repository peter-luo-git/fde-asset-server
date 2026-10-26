"""技能包：试跑和模型通读的结果、公开技能库来源。

Revision ID: 0013_skill_packages
Revises: 0012_sop_execution
Create Date: 2026-10-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_skill_packages"
down_revision: Union[str, None] = "0012_sop_execution"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if "skill_checks" not in tables:
        op.create_table(
            "skill_checks",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("digest", sa.String(64), nullable=False),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("candidate_id", sa.String(32), nullable=False, server_default=""),
            sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("result_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("claimed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("digest", "kind", name="uq_skill_check"),
        )
    if "skill_sources" not in tables:
        op.create_table(
            "skill_sources",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(100), nullable=False),
            sa.Column("url", sa.String(500), nullable=False),
            sa.Column("branch", sa.String(100), nullable=False, server_default=""),
            sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("status", sa.String(16), nullable=False, server_default=""),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("commit_sha", sa.String(64), nullable=False, server_default=""),
            sa.Column("catalog_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("checked_at", sa.DateTime(timezone=True)),
            sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("url", name="uq_skill_source_url"),
        )


def downgrade() -> None:
    op.drop_table("skill_sources")
    op.drop_table("skill_checks")
