"""附件的图片理解结果：按附件内容的 SHA-256 存带图片说明的文字版。

Revision ID: 0015_attachment_texts
Revises: 0014_skill_deps_status
Create Date: 2026-10-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_attachment_texts"
down_revision: Union[str, None] = "0014_skill_deps_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "attachment_texts" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "attachment_texts",
            sa.Column("sha256", sa.String(64), primary_key=True),
            sa.Column("filename", sa.String(255), nullable=False, server_default=""),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("done", sa.Integer, nullable=False, server_default="0"),
            sa.Column("total", sa.Integer, nullable=False, server_default="0"),
            sa.Column("text", sa.Text, nullable=False, server_default=""),
            sa.Column("report_json", sa.Text, nullable=False, server_default="{}"),
            sa.Column("include_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("error", sa.Text, nullable=False, server_default=""),
            sa.Column("claimed_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True)),
        )


def downgrade() -> None:
    op.drop_table("attachment_texts")
