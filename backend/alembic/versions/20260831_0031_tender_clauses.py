"""创建可溯源招标条款事实层。

Revision ID: 20260831_0031
Revises: 20260831_0030
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0031"
down_revision: str | Sequence[str] | None = "20260831_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tender_clauses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "document_version_id",
            sa.Uuid(),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("clause_type", sa.String(32), nullable=False),
        sa.Column("section_path", sa.String(1024)),
        sa.Column("start_page", sa.Integer()),
        sa.Column("end_page", sa.Integer()),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("contextualized_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("mandatory_signal", sa.Boolean(), nullable=False),
        sa.Column("quality_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_version_id", "order_no"),
    )
    op.create_index(
        "ix_tender_clauses_document_version_id", "tender_clauses", ["document_version_id"]
    )
    op.create_table(
        "clause_evidences",
        sa.Column(
            "clause_id",
            sa.Uuid(),
            sa.ForeignKey("tender_clauses.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_id",
            sa.Uuid(),
            sa.ForeignKey("evidences.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("relation", sa.String(32), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("clause_evidences")
    op.drop_index("ix_tender_clauses_document_version_id", table_name="tender_clauses")
    op.drop_table("tender_clauses")
