"""修复已有文档项目仍为草稿的状态。

Revision ID: 20260906_0050
Revises: 20260906_0049
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260906_0050"
down_revision: str | Sequence[str] | None = "20260906_0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE tender_projects AS project
        SET status = 'ACTIVE'
        WHERE project.status = 'DRAFT'
          AND EXISTS (
              SELECT 1 FROM project_documents AS document
              WHERE document.project_id = project.id
          )
        """
    )


def downgrade() -> None:
    # 无法安全区分本迁移前就处于 ACTIVE 的项目，保持状态不回退。
    pass
