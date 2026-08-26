"""add WAITING_HUMAN_REVIEW to parse_status enum

用于 LangGraph 文档流水线人工复核等待状态。
"""

from alembic import op

revision = "202608240100"
down_revision = "202608240000"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute("ALTER TYPE app.parse_status ADD VALUE IF NOT EXISTS 'WAITING_HUMAN_REVIEW'")


def downgrade() -> None:
    # Postgres enum 不支持删除值，留空
    pass
