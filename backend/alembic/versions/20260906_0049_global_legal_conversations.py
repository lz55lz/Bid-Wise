"""允许无项目归属的全局法律会话。

Revision ID: 20260906_0049
Revises: 20260903_0048
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260906_0049"
down_revision: str | Sequence[str] | None = "20260903_0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("conversations", "project_id", nullable=True)


def downgrade() -> None:
    # 只有在不存在全局会话时才允许降级，避免静默丢失会话归属。
    op.execute("DELETE FROM conversations WHERE project_id IS NULL")
    op.alter_column("conversations", "project_id", nullable=False)
