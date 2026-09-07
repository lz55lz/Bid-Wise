"""持久化项目与知识文件的解析阶段进度。"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0038"
down_revision: str | Sequence[str] | None = "20260901_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _add_progress(table: str) -> None:
    op.add_column(
        table, sa.Column("progress_stage", sa.String(32), nullable=False, server_default="QUEUED")
    )
    op.add_column(
        table, sa.Column("progress_percent", sa.Integer(), nullable=False, server_default="0")
    )
    op.add_column(table, sa.Column("progress_message", sa.String(256)))
    op.add_column(
        table,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def upgrade() -> None:
    _add_progress("document_parse_jobs")
    _add_progress("knowledge_parse_jobs")


def downgrade() -> None:
    for table in ("knowledge_parse_jobs", "document_parse_jobs"):
        op.drop_column(table, "updated_at")
        op.drop_column(table, "progress_message")
        op.drop_column(table, "progress_percent")
        op.drop_column(table, "progress_stage")
