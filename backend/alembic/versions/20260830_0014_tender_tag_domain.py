# ruff: noqa: E501
"""建立 bid_pipeline 的标签领域事实表。

该迁移只创建结构，不把几十 KB 的受控标签数据硬编码进 DDL。标准标签库由同版本
的初始化器幂等导入，便于部署、重跑和审计，也避免迁移脚本依赖旧项目的运行配置。

Revision ID: 20260830_0014
Revises: 20260830_0013
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260830_0014"
down_revision: str | Sequence[str] | None = "20260830_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """创建标签字典、关系和版本化提取事实表。"""
    # 标签库种子使用数据库生成 UUID；pgcrypto 同时是 PostgreSQL 的标准扩展，
    # 不依赖 API 进程的 Python UUID 生成逻辑。
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.create_table(
        "tender_tag_categories",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "tender_tag_levels",
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("code"),
    )
    op.create_table(
        "tender_tags",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("category_code", sa.String(length=20), nullable=False),
        sa.Column("level_code", sa.String(length=10), nullable=False),
        sa.Column("data_type", sa.String(length=30), nullable=False),
        sa.Column("is_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_multi_value", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extraction_prompt", sa.Text()),
        sa.Column("value_example", sa.Text()),
        sa.Column("validation_regex", sa.Text()),
        sa.Column("remark", sa.Text()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["category_code"], ["tender_tag_categories.code"]),
        sa.ForeignKeyConstraint(["level_code"], ["tender_tag_levels.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_index("ix_tender_tags_category_code", "tender_tags", ["category_code"])
    op.create_table(
        "tender_tag_relations",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_tag_code", sa.String(length=80), nullable=False),
        sa.Column("target_tag_code", sa.String(length=80), nullable=False),
        sa.Column("relation_type", sa.String(length=24), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("rule", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["source_tag_code"], ["tender_tags.code"]),
        sa.ForeignKeyConstraint(["target_tag_code"], ["tender_tags.code"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_tag_code", "target_tag_code", "relation_type"),
    )
    op.create_table(
        "tender_document_tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_version_id", sa.Uuid(), nullable=False),
        sa.Column("tag_code", sa.String(length=80), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text())),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("source_document_node_id", sa.Uuid()),
        sa.Column("source_page_number", sa.Integer()),
        sa.Column("source_text", sa.Text()),
        sa.Column("extract_method", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=80)),
        sa.Column(
            "review_status", sa.String(length=20), nullable=False, server_default="UNREVIEWED"
        ),
        sa.Column("validation_issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("reviewed_by", sa.Uuid()),
        sa.CheckConstraint(
            "extract_method IN ('KEYWORD', 'LLM', 'VECTOR', 'HUMAN')",
            name="tender_document_tags_method_check",
        ),
        sa.CheckConstraint(
            "review_status IN ('UNREVIEWED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED')",
            name="tender_document_tags_review_check",
        ),
        sa.ForeignKeyConstraint(["document_version_id"], ["document_versions.id"]),
        sa.ForeignKeyConstraint(["tag_code"], ["tender_tags.code"]),
        sa.ForeignKeyConstraint(["source_document_node_id"], ["document_nodes.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_tender_document_tags_document_version_id",
        "tender_document_tags",
        ["document_version_id"],
    )
    op.create_index("ix_tender_document_tags_tag_code", "tender_document_tags", ["tag_code"])
    op.create_index(
        "ix_tender_document_tags_source_document_node_id",
        "tender_document_tags",
        ["source_document_node_id"],
    )


def downgrade() -> None:
    """开发期回退结构；生产数据回退应使用备份恢复。"""
    op.drop_index(
        "ix_tender_document_tags_source_document_node_id", table_name="tender_document_tags"
    )
    op.drop_index("ix_tender_document_tags_tag_code", table_name="tender_document_tags")
    op.drop_index("ix_tender_document_tags_document_version_id", table_name="tender_document_tags")
    op.drop_table("tender_document_tags")
    op.drop_table("tender_tag_relations")
    op.drop_index("ix_tender_tags_category_code", table_name="tender_tags")
    op.drop_table("tender_tags")
    op.drop_table("tender_tag_levels")
    op.drop_table("tender_tag_categories")
