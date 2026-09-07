"""风险结果收敛为项目当前快照，不再保留 is_current 历史行。

Revision ID: 20260903_0047
Revises: 20260903_0046
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0047"
down_revision: str | Sequence[str] | None = "20260903_0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 旧设计用 is_current=False 保存历史风险；产品语义已确定为“只保留当前结果”。
    op.execute("DELETE FROM project_risks WHERE is_current = false")
    op.drop_column("project_risks", "is_current")


def downgrade() -> None:
    op.add_column(
        "project_risks",
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column("project_risks", "is_current", server_default=None)
