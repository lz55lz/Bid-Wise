"""add pipeline_thread_id to document_versions

用于 LangGraph interrupt_before checkpoint 恢复。
"""

import sqlalchemy as sa
from alembic import op

revision = "202608220000"
down_revision = "202608210000"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("pipeline_thread_id", sa.String(128), nullable=True),
        schema="app",
    )


def downgrade() -> None:
    op.drop_column("document_versions", "pipeline_thread_id", schema="app")
