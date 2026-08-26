"""Add content_type, parent_chunk_id, start_at, end_at to search_chunks.

WeKnora-style content types for fine-grained chunk classification:
- text: 文本块（索引粒度）
- parent_text: 父文本块（不上索引，只提供上下文）
- faq: FAQ 问答块
- summary: 摘要块
- image_ocr: 图片 OCR 块
- image_caption: 图片描述块

Revision ID: 202608140000
Revises: 202608139999
Create Date: 2026-08-14

"""
from alembic import op
import sqlalchemy as sa


revision = "202608140000"
down_revision = "202608139999"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # content_type: WeKnora 风格内容类型
    op.add_column(
        "search_chunks",
        sa.Column("content_type", sa.String(32), nullable=True),
    )
    # 父块 ID（用于父子块体系）
    op.add_column(
        "search_chunks",
        sa.Column("parent_chunk_id", sa.UUID(), nullable=True),
    )
    # 原文 rune 偏移（用于高亮和内容还原）
    op.add_column(
        "search_chunks",
        sa.Column("start_at", sa.Integer(), nullable=True),
    )
    op.add_column(
        "search_chunks",
        sa.Column("end_at", sa.Integer(), nullable=True),
    )
    # FAQ metadata（标准问题/答案/相似问）
    from sqlalchemy.dialects.postgresql import JSONB
    op.add_column(
        "search_chunks",
        sa.Column("faq_metadata", JSONB(), nullable=True),
    )

    # 初始化 content_type = 'text'（现有 chunk 的默认值）
    op.execute("UPDATE app.search_chunks SET content_type = 'text'")

    # 设置 NOT NULL
    op.alter_column("search_chunks", "content_type", nullable=False)

    # 索引
    op.create_index(
        "ix_search_chunks_content_type",
        "search_chunks",
        ["content_type"],
        unique=False,
    )
    op.create_index(
        "ix_search_chunks_parent_chunk_id",
        "search_chunks",
        ["parent_chunk_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_search_chunks_parent_chunk_id",
        "search_chunks",
        "search_chunks",
        ["parent_chunk_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_search_chunks_parent_chunk_id", "search_chunks", type_="foreignkey")
    op.drop_index("ix_search_chunks_parent_chunk_id", "search_chunks")
    op.drop_index("ix_search_chunks_content_type", "search_chunks")
    op.drop_column("search_chunks", "faq_metadata")
    op.drop_column("search_chunks", "end_at")
    op.drop_column("search_chunks", "start_at")
    op.drop_column("search_chunks", "parent_chunk_id")
    op.drop_column("search_chunks", "content_type")
