"""必选资产：管理员或范围负责人指定，在这个范围里干活的 Agent 一定带上。

Revision ID: 0009_asset_mandatory
Revises: 0008_app_health_source
Create Date: 2026-10-17
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_asset_mandatory"
down_revision: Union[str, None] = "0008_app_health_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 0001 按当时的代码建全部表，新库上这张表已经建好了
    if "asset_mandatory" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "asset_mandatory",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("asset_id", sa.String(64), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("asset_id", "scope", "code", name="uq_asset_mandatory"),
    )
    op.create_index("ix_asset_mandatory_lookup", "asset_mandatory", ["scope", "code"])


def downgrade() -> None:
    op.drop_index("ix_asset_mandatory_lookup", table_name="asset_mandatory")
    op.drop_table("asset_mandatory")
