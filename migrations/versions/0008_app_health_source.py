"""探活结果记下是谁探的：平台自己，还是某个用户的电脑替它探的。

Revision ID: 0008_app_health_source
Revises: 0007_candidate_customer
Create Date: 2026-10-06
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_app_health_source"
down_revision: Union[str, None] = "0007_candidate_customer"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    """0001 是按当时的代码建全部表的；在新库上，后来才加的列它已经一并建好了，这里就不能再加一次。"""
    return column in {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_column("app_health", "checked_via"):
        op.add_column(
            "app_health",
            sa.Column("checked_via", sa.String(16), nullable=False, server_default="server"),
        )
    if not _has_column("app_health", "checked_by"):
        op.add_column(
            "app_health", sa.Column("checked_by", sa.String(64), nullable=False, server_default="")
        )


def downgrade() -> None:
    op.drop_column("app_health", "checked_by")
    op.drop_column("app_health", "checked_via")
