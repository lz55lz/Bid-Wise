"""为项目报告添加可检索新鲜度标记。"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260901_0037"
down_revision: str | Sequence[str] | None = "20260901_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project_reports",
        sa.Column("is_stale", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_project_reports_is_stale", "project_reports", ["is_stale"])


def downgrade() -> None:
    op.drop_index("ix_project_reports_is_stale", table_name="project_reports")
    op.drop_column("project_reports", "is_stale")
