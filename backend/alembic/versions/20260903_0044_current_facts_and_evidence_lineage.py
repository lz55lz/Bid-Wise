"""建立 Evidence 多节点血缘，并把派生业务结果收敛为当前投影。

Revision ID: 20260903_0044
Revises: 20260903_0043
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_0044"
down_revision: str | Sequence[str] | None = "20260903_0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "evidence_source_nodes",
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("document_node_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidences.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_node_id"], ["document_nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("evidence_id", "document_node_id"),
    )
    op.create_index(
        "ix_evidence_source_nodes_document_node_id",
        "evidence_source_nodes",
        ["document_node_id"],
    )
    # 新结构化 Evidence 已把完整来源放在 locator.source_node_ids。先按该数组回填，
    # 再用旧 anchor FK 兜底，保证迁移后的历史 Evidence 也能参与正式血缘查询。
    op.execute(
        """
        INSERT INTO evidence_source_nodes (evidence_id, document_node_id, ordinal)
        SELECT e.id, src.node_id::uuid, src.ordinality::integer
        FROM evidences AS e
        CROSS JOIN LATERAL jsonb_array_elements_text(
            COALESCE(e.locator -> 'source_node_ids', '[]'::jsonb)
        ) WITH ORDINALITY AS src(node_id, ordinality)
        JOIN document_nodes AS n ON n.id = src.node_id::uuid
        ON CONFLICT (evidence_id, document_node_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO evidence_source_nodes (evidence_id, document_node_id, ordinal)
        SELECT e.id, e.document_node_id, 1
        FROM evidences AS e
        WHERE e.document_node_id IS NOT NULL
        ON CONFLICT (evidence_id, document_node_id) DO NOTHING
        """
    )

    op.add_column(
        "tender_document_tags",
        sa.Column("source_evidence_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_tender_document_tags_source_evidence",
        "tender_document_tags",
        "evidences",
        ["source_evidence_id"],
        ["id"],
    )
    op.create_index(
        "ix_tender_document_tags_source_evidence_id",
        "tender_document_tags",
        ["source_evidence_id"],
    )
    # 能从旧 source_document_node_id 映射到 Evidence 的历史标签一并补上。
    op.execute(
        """
        UPDATE tender_document_tags AS t
        SET source_evidence_id = source.evidence_id
        FROM (
            SELECT DISTINCT ON (document_node_id) document_node_id, evidence_id
            FROM evidence_source_nodes
            ORDER BY document_node_id, ordinal, evidence_id
        ) AS source
        WHERE t.source_evidence_id IS NULL
          AND t.source_document_node_id = source.document_node_id
        """
    )

    op.add_column(
        "tender_projects",
        sa.Column("bid_deadline_source", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE tender_projects SET bid_deadline_source = 'MANUAL' "
        "WHERE bid_deadline IS NOT NULL"
    )
    op.create_check_constraint(
        "tender_projects_bid_deadline_source_check",
        "tender_projects",
        "bid_deadline_source IS NULL OR bid_deadline_source IN ('MANUAL', 'PIPELINE')",
    )

    # 报告不是审计事实：每个项目只保留当前报告。迁移时只保留最近完成/创建的一条。
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY project_id
                       ORDER BY completed_at DESC NULLS LAST, created_at DESC, id DESC
                   ) AS rn
            FROM project_reports
        )
        DELETE FROM project_reports
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.drop_column("project_reports", "version_no")
    op.execute("DROP INDEX IF EXISTS ux_project_reports_active_project")
    op.create_unique_constraint(
        "uq_project_reports_project", "project_reports", ["project_id"]
    )
    # 旧代码没有数据库唯一约束，极端并发下可能已经存在多个 PUBLISHED。先保留
    # 每个条目最近发布的一版并把其余版本降回 DRAFT，再创建唯一索引，避免升级迁移
    # 因历史脏数据直接失败。
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY knowledge_entry_id
                       ORDER BY published_at DESC NULLS LAST, created_at DESC, id DESC
                   ) AS rn
            FROM knowledge_versions
            WHERE status = 'PUBLISHED'
        )
        UPDATE knowledge_versions AS v
        SET status = 'DRAFT', published_at = NULL, published_by = NULL
        FROM ranked
        WHERE v.id = ranked.id AND ranked.rn > 1
        """
    )
    # 数据库层再兜底：同一知识条目最多一个已发布版本。服务层同时锁 entry 行，
    # 让并发发布具有确定的“后提交版本成为当前版本”语义。
    op.execute(
        "CREATE UNIQUE INDEX ux_knowledge_versions_published_entry "
        "ON knowledge_versions (knowledge_entry_id) WHERE status = 'PUBLISHED'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_knowledge_versions_published_entry")
    op.add_column(
        "project_reports",
        sa.Column("version_no", sa.Integer(), nullable=False, server_default="1"),
    )
    op.drop_constraint("uq_project_reports_project", "project_reports", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX ux_project_reports_active_project "
        "ON project_reports (project_id) WHERE status IN ('QUEUED', 'GENERATING')"
    )
    op.drop_constraint(
        "tender_projects_bid_deadline_source_check", "tender_projects", type_="check"
    )
    op.drop_column("tender_projects", "bid_deadline_source")
    op.drop_index(
        "ix_tender_document_tags_source_evidence_id",
        table_name="tender_document_tags",
    )
    op.drop_constraint(
        "fk_tender_document_tags_source_evidence",
        "tender_document_tags",
        type_="foreignkey",
    )
    op.drop_column("tender_document_tags", "source_evidence_id")
    op.drop_index(
        "ix_evidence_source_nodes_document_node_id",
        table_name="evidence_source_nodes",
    )
    op.drop_table("evidence_source_nodes")
