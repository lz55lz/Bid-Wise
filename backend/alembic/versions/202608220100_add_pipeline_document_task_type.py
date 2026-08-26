"""add PIPELINE_DOCUMENT to task_type enum

配合 LangGraph 文档流水线任务类型。
"""

from alembic import op

revision = "202608220100"
down_revision = "202608220000"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE app.task_type ADD VALUE IF NOT EXISTS 'PIPELINE_DOCUMENT'")


def downgrade() -> None:
    # Postgres enum 不支持删除值，留空
    pass
