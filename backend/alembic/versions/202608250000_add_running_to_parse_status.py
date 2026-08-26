"""add RUNNING and SUCCEEDED to parse_status enum

PipelineStateManager 使用 RUNNING 表示执行中，SUCCEEDED 表示 pipeline 完成。
"""

from alembic import op

revision = "202608250000"
down_revision = "202608240100"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE app.parse_status ADD VALUE IF NOT EXISTS 'RUNNING'")
    op.execute("ALTER TYPE app.parse_status ADD VALUE IF NOT EXISTS 'SUCCEEDED'")


def downgrade() -> None:
    pass
