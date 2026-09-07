"""将文档定位元数据统一为 PostgreSQL JSONB。

Revision ID: 20260830_0012
Revises: 20260830_0011
Create Date: 2026-08-30
"""

from collections.abc import Sequence

from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0012"
down_revision: str | Sequence[str] | None = "20260830_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """JSONB 更适合定位元数据的键查询与索引，且与 ORM 声明保持一致。"""
    op.alter_column(
        "document_nodes",
        "metadata",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="metadata::jsonb",
    )
    op.alter_column(
        "evidences",
        "locator",
        existing_type=postgresql.JSON(),
        type_=postgresql.JSONB(),
        postgresql_using="locator::jsonb",
    )


def downgrade() -> None:
    """仅供开发期回退；生产环境不应依赖回退迁移处理业务数据。"""
    op.alter_column(
        "evidences",
        "locator",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        postgresql_using="locator::json",
    )
    op.alter_column(
        "document_nodes",
        "metadata",
        existing_type=postgresql.JSONB(),
        type_=postgresql.JSON(),
        postgresql_using="metadata::json",
    )
