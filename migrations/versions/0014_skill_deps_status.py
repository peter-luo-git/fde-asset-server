"""技能依赖在 Agent 机器上的安装状态（worker 回报，用于评审页显示和个人容量限额）。

Revision ID: 0014_skill_deps_status
Revises: 0013_skill_packages
Create Date: 2026-10-26
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_skill_deps_status"
down_revision: Union[str, None] = "0013_skill_packages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if "skill_deps_status" not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            "skill_deps_status",
            sa.Column("key", sa.String(64), primary_key=True),
            sa.Column("requirements_json", sa.Text, nullable=False, server_default="[]"),
            sa.Column("state", sa.String(16), nullable=False, server_default="missing"),
            sa.Column("bytes", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("reason", sa.Text, nullable=False, server_default=""),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("skill_deps_status")
