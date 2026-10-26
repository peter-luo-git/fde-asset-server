"""SOP 只用于执行：任务绑定记版本和检查点状态，新增默认流程。

Revision ID: 0012_sop_execution
Revises: 0011_checkup_findings
Create Date: 2026-10-25
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_sop_execution"
down_revision: Union[str, None] = "0011_checkup_findings"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {item["name"] for item in inspector.get_columns("work_item_assets")}
    if "sop_version" not in columns:
        op.add_column(
            "work_item_assets",
            sa.Column("sop_version", sa.String(32), nullable=False, server_default=""),
        )
    if "progress_json" not in columns:
        op.add_column(
            "work_item_assets",
            sa.Column("progress_json", sa.Text, nullable=False, server_default="{}"),
        )
    if "updated_at" not in columns:
        op.add_column("work_item_assets", sa.Column("updated_at", sa.DateTime(timezone=True)))
    if "sop_defaults" not in set(inspector.get_table_names()):
        op.create_table(
            "sop_defaults",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("scope", sa.String(16), nullable=False),
            sa.Column("code", sa.String(64), nullable=False, server_default=""),
            sa.Column("asset_id", sa.String(64), nullable=False),
            sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("scope", "code", name="uq_sop_default_scope"),
        )


def downgrade() -> None:
    op.drop_table("sop_defaults")
    op.drop_column("work_item_assets", "updated_at")
    op.drop_column("work_item_assets", "progress_json")
    op.drop_column("work_item_assets", "sop_version")
