# ruff: noqa: E501
"""招标标签库与文档标签事实模型。

标签库是受控的系统基线数据：标签编码稳定、可被工作流和规则引用。文档标签则是
某个不可变文档版本的一次提取事实，必须保存证据节点和置信度，不能只保存一个
脱离原文的 JSON 值。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderTagCategory(Base):
    """标签分类，例如项目基本信息、资格要求和否决性条款。"""

    __tablename__ = "tender_tag_categories"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderTagLevel(Base):
    """提取优先级：P0 关键必填、P1 重要推荐、P2 一般可选。"""

    __tablename__ = "tender_tag_levels"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TenderTag(Base):
    """受控标签字典。

    ``extraction_prompt`` 是该字段的业务抽取约束，而不是可由前端覆盖的模型提示词；
    修改它会影响后续工作流的提取行为，应通过受审计的标签管理能力处理。
    """

    __tablename__ = "tender_tags"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    category_code: Mapped[str] = mapped_column(
        ForeignKey("tender_tag_categories.code"), nullable=False, index=True
    )
    level_code: Mapped[str] = mapped_column(ForeignKey("tender_tag_levels.code"), nullable=False)
    data_type: Mapped[str] = mapped_column(String(30), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_multi_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extraction_prompt: Mapped[str | None] = mapped_column(Text)
    value_example: Mapped[str | None] = mapped_column(Text)
    validation_regex: Mapped[str | None] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderTagRelation(Base):
    """标签间的依赖、冲突或联动关系，供校验和下游规则使用。"""

    __tablename__ = "tender_tag_relations"
    __table_args__ = (UniqueConstraint("source_tag_code", "target_tag_code", "relation_type"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_tag_code: Mapped[str] = mapped_column(ForeignKey("tender_tags.code"), nullable=False)
    target_tag_code: Mapped[str] = mapped_column(ForeignKey("tender_tags.code"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    rule: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderDocumentTag(Base):
    """某个文档版本中一个标签的结构化提取结果。

    一个标签可在多个节点命中，因此不以 ``(version, tag_code)`` 强行唯一；同一轮
    工作流会通过 ``pipeline_run_id`` 归属，后续由汇总规则确定当前生效值。
    """

    __tablename__ = "tender_document_tags"
    __table_args__ = (
        CheckConstraint(
            "extract_method IN ('KEYWORD', 'LLM', 'VECTOR', 'HUMAN')",
            name="tender_document_tags_method_check",
        ),
        CheckConstraint(
            "review_status IN ('UNREVIEWED', 'PENDING_REVIEW', 'APPROVED', 'REJECTED')",
            name="tender_document_tags_review_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # 每次运行各自保留自动结果与人工审核事实，重跑不覆盖历史审计记录。
    pipeline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_pipeline_runs.id"), index=True
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )
    tag_code: Mapped[str] = mapped_column(
        ForeignKey("tender_tags.code"), nullable=False, index=True
    )
    value: Mapped[object | None] = mapped_column(JSONB)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source_evidence_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("evidences.id"), index=True
    )
    source_document_node_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_nodes.id"), index=True
    )
    source_page_number: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    extract_method: Mapped[str] = mapped_column(String(16), nullable=False)
    model_id: Mapped[str | None] = mapped_column(String(80))
    review_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNREVIEWED")
    validation_issues: Mapped[list[object]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class TenderPipelineRun(Base):
    """一份文档版本的一次完整 bid_pipeline 运行事实。

    ``thread_id`` 同时是 LangGraph checkpointer 的线程标识。运行状态属于业务事实，
    因此即使图的 checkpoint 仍可读，API 也绝不直接按 thread_id 向用户暴露内容。
    """

    __tablename__ = "tender_pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'RESUME_QUEUED', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="tender_pipeline_runs_status_check",
        ),
        Index(
            "ux_tender_pipeline_runs_active_document_version",
            "document_version_id",
            unique=True,
            postgresql_where=(
                "status IN ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW', 'RESUME_QUEUED')"
            ),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id"), nullable=False, index=True
    )
    requested_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    thread_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    arq_job_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    downstream_analysis_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("project_analysis_runs.id"), index=True
    )
    pending_review: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TenderPipelineStage(Base):
    """一次管线运行中每个节点的业务可见状态与脱敏摘要。"""

    __tablename__ = "tender_pipeline_stages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'WAITING_HUMAN_REVIEW', 'SKIPPED')",
            name="tender_pipeline_stages_status_check",
        ),
        UniqueConstraint("pipeline_run_id", "stage_name"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    pipeline_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(48), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING")
    input_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    output_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
