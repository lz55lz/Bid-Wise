"""冻结知识版本的引用元数据，避免草稿元数据污染已发布正文。

Revision ID: 20260903_0048
Revises: 20260903_0047
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0048"
down_revision: str | Sequence[str] | None = "20260903_0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_versions", sa.Column("title", sa.String(length=512), nullable=True))
    op.add_column("knowledge_versions", sa.Column("authority", sa.String(length=256), nullable=True))
    op.add_column(
        "knowledge_versions", sa.Column("source_reference", sa.String(length=1024), nullable=True)
    )
    op.add_column(
        "knowledge_versions", sa.Column("issued_on", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "knowledge_versions", sa.Column("effective_on", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("knowledge_versions", sa.Column("citation_note", sa.Text(), nullable=True))

    # 历史版本过去共用 KnowledgeEntry 元数据。迁移时只能以现有条目值作为其冻结值；
    # 从本迁移开始，新草稿的元数据会随版本独立保存，发布时再投影回 entry 摘要。
    op.execute(
        """
        UPDATE knowledge_versions AS v
        SET title = e.title,
            authority = e.authority,
            source_reference = e.source_reference,
            issued_on = e.issued_on,
            effective_on = e.effective_on,
            citation_note = e.citation_note
        FROM knowledge_entries AS e
        WHERE e.id = v.knowledge_entry_id
        """
    )
    op.alter_column("knowledge_versions", "title", nullable=False)
    op.alter_column("knowledge_versions", "source_reference", nullable=False)


def downgrade() -> None:
    op.drop_column("knowledge_versions", "citation_note")
    op.drop_column("knowledge_versions", "effective_on")
    op.drop_column("knowledge_versions", "issued_on")
    op.drop_column("knowledge_versions", "source_reference")
    op.drop_column("knowledge_versions", "authority")
    op.drop_column("knowledge_versions", "title")
