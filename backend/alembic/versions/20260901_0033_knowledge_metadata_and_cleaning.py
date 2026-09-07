"""保存知识来源元数据与解析清洗摘要。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_0033"
down_revision: str | Sequence[str] | None = "20260831_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("knowledge_entries", sa.Column("issued_on", sa.DateTime(timezone=True)))
    op.add_column("knowledge_entries", sa.Column("effective_on", sa.DateTime(timezone=True)))
    op.add_column("knowledge_entries", sa.Column("citation_note", sa.Text()))
    op.add_column("knowledge_document_versions", sa.Column("cleaning_summary", sa.JSON()))


def downgrade() -> None:
    op.drop_column("knowledge_document_versions", "cleaning_summary")
    op.drop_column("knowledge_entries", "citation_note")
    op.drop_column("knowledge_entries", "effective_on")
    op.drop_column("knowledge_entries", "issued_on")
