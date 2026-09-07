"""补全公共知识源的解析任务和可重建向量块。"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_0027"
down_revision: str | Sequence[str] | None = "20260831_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_document_versions", sa.Column("file_size", sa.BigInteger(), nullable=True)
    )
    op.add_column("knowledge_document_versions", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("knowledge_document_versions", sa.Column("parse_output_key", sa.String(1024)))
    op.add_column("knowledge_document_versions", sa.Column("error_code", sa.String(80)))
    op.add_column("knowledge_document_versions", sa.Column("error_message", sa.Text()))
    op.add_column(
        "knowledge_document_versions", sa.Column("completed_at", sa.DateTime(timezone=True))
    )
    op.execute("UPDATE knowledge_document_versions SET file_size = 0, sha256 = ''")
    op.alter_column("knowledge_document_versions", "file_size", nullable=False)
    op.alter_column("knowledge_document_versions", "sha256", nullable=False)
    op.create_table(
        "knowledge_parse_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_document_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("arq_job_id", sa.String(128)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="knowledge_parse_jobs_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_document_version_id"],
            ["knowledge_document_versions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_document_version_id"),
        sa.UniqueConstraint("arq_job_id"),
    )
    op.create_table(
        "knowledge_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("knowledge_version_id", sa.Uuid(), nullable=False),
        sa.Column("order_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("section_path", sa.String(1024)),
        sa.Column("embedding", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["knowledge_version_id"], ["knowledge_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("knowledge_version_id", "order_no"),
    )
    op.execute(
        "ALTER TABLE knowledge_chunks ALTER COLUMN embedding TYPE vector(1024) "
        "USING embedding::vector"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_version ON knowledge_chunks (knowledge_version_id)"
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_hnsw ON knowledge_chunks USING hnsw "
        "(embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_parse_jobs")
    for column in (
        "completed_at",
        "error_message",
        "error_code",
        "parse_output_key",
        "sha256",
        "file_size",
    ):
        op.drop_column("knowledge_document_versions", column)
