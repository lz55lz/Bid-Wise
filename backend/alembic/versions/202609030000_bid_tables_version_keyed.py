"""bid_* 结果表改挂 document_version_id（本地库，数据可弃）

背景：
  bid_pipeline 新链路以 app.document_versions/document_nodes 为事实源，
  旧链路的 bid_document.doc_id 已不再创建（worker 传 doc_id=0），
  导致 bid_document_tag / bid_risk / bid_report / bid_task_log 全部读写落空。

本次迁移（数据不保留）：
  - bid_document_tag: 重建，键改为 (version_id, tag_id)，source_chunk_id → source_node_id（document_nodes.id）
  - bid_risk: 重建，键改为 version_id，source_chunks 改 TEXT[]（节点 UUID）
  - bid_report: 重建，键改为 version_id（保留可空 doc_id 供旧 /bid 原型接口查询）
  - bid_task_log: 重建，键改为 version_id + thread_id，补 created_at 列
  - bid_document / bid_doc_chunk 保留（旧 /bid 原型上传接口仍在用），pipeline 不再读写
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision = "202609030000"
down_revision = "202609020000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("bid_document_tag")
    op.create_table(
        "bid_document_tag",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("tag_id", sa.BigInteger, nullable=False),
        sa.Column("tag_value", sa.Text),
        sa.Column("tag_value_json", JSONB),
        sa.Column("source_text", sa.Text),
        sa.Column("source_node_id", sa.Text),
        sa.Column("source_page", sa.Integer),
        sa.Column("confidence", sa.Numeric(5, 2)),
        sa.Column("extract_method", sa.String(20)),
        sa.Column("llm_model", sa.String(50)),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("reviewed", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("reviewer", sa.String(100)),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("remark", sa.Text),
        sa.UniqueConstraint("version_id", "tag_id", name="uq_bid_doc_tag_version"),
    )
    op.create_index("idx_bid_doc_tag_version", "bid_document_tag", ["version_id"])

    op.drop_table("bid_risk")
    op.create_table(
        "bid_risk",
        sa.Column("risk_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("risk_type", sa.String(40), nullable=False),
        sa.Column("risk_level", sa.String(10), nullable=False),
        sa.Column("risk_title", sa.String(500), nullable=False),
        sa.Column("risk_desc", sa.Text),
        sa.Column("related_tags", ARRAY(sa.String())),
        sa.Column("source_chunks", ARRAY(sa.Text())),
        sa.Column("suggestion", sa.Text),
        sa.Column("confidence", sa.Numeric(5, 2)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_bid_risk_version", "bid_risk", ["version_id", "risk_level", "risk_type"])

    op.drop_table("bid_report")
    op.create_table(
        "bid_report",
        sa.Column("report_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False, unique=True),
        sa.Column("doc_id", sa.BigInteger, nullable=True),
        sa.Column("decision", sa.String(20)),
        sa.Column("overall_score", sa.Numeric(5, 2)),
        sa.Column("qualification_score", sa.Numeric(5, 2)),
        sa.Column("risk_score", sa.Numeric(5, 2)),
        sa.Column("trap_score", sa.Numeric(5, 2)),
        sa.Column("competition_score", sa.Numeric(5, 2)),
        sa.Column("summary", sa.Text),
        sa.Column("report_md", sa.Text),
        sa.Column("report_json", JSONB),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_bid_report_doc", "bid_report", ["doc_id"])

    op.drop_table("bid_task_log")
    op.create_table(
        "bid_task_log",
        sa.Column("task_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("version_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", sa.String(100)),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("node_name", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt", sa.Integer, server_default="1"),
        sa.Column("max_attempts", sa.Integer, server_default="3"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Integer),
        sa.Column("input_summary", sa.Text),
        sa.Column("output_summary", sa.Text),
        sa.Column("error_msg", sa.Text),
        sa.Column("payload", JSONB),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "idx_bid_task_log_resume", "bid_task_log", ["version_id", "thread_id", "stage", "status"]
    )


def downgrade() -> None:
    # 数据已弃置，downgrade 仅删表
    for table in ("bid_document_tag", "bid_risk", "bid_report", "bid_task_log"):
        op.drop_table(table)
