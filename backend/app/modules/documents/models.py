"""项目文档的最小事实模型。

此模块只管理项目上传的招标文件及其解析生命周期。法规知识库、企业材料、向量节点
和 Evidence 分别在后续领域迁移，不能重新堆回一个“大文档表”。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectDocument(Base):
    """项目内的逻辑文档；同一文档可保留多个不可变文件版本。"""

    __tablename__ = "project_documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"),
        nullable=False,
        index=True,
    )
    logical_name: Mapped[str] = mapped_column(String(512), nullable=False)
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "document_versions.id",
            name="fk_project_documents_current_version",
            use_alter=True,
        ),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class DocumentVersion(Base):
    """上传源文件的不可变版本及其解析状态。"""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no"),
        CheckConstraint(
            "parse_status IN ('UPLOADED', 'QUEUED', 'PARSING', 'CLEANING', "
            "'BUILDING_EVIDENCE', 'INDEXING', 'READY', 'FAILED')",
            name="document_versions_parse_status_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("project_documents.id"),
        nullable=False,
        index=True,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    parse_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    parse_output_key: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    # 解析事实与清洗派生结果分开保存。摘要只记录清洗统计，不能替代逐节点
    # 的过滤原因；下游管线据此判断是否允许进入条款与检索阶段。
    cleaning_summary: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentParseJob(Base):
    """文档解析的业务任务记录，与 ARQ 运行时 job_id 一一关联。"""

    __tablename__ = "document_parse_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="document_parse_jobs_status_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id"),
        nullable=False,
        unique=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    arq_job_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(256))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class DocumentNode(Base):
    """解析后可定位的文档节点，是后续 Evidence 和 RAG 的原始依据。"""

    __tablename__ = "document_nodes"
    __table_args__ = (
        CheckConstraint(
            "node_type IN ('SECTION', 'PARAGRAPH', 'TABLE', 'LIST', 'IMAGE')",
            name="document_nodes_node_type_check",
        ),
        UniqueConstraint("document_version_id", "order_no"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id"),
        nullable=False,
        index=True,
    )
    # MinerU 版面块可形成树：标题是父节点，正文/表格是其子节点。该关系用于
    # 检索命中子块后回取完整章节上下文，不能只靠 section_path 字符串猜测。
    parent_node_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_nodes.id", ondelete="RESTRICT"), index=True
    )
    node_type: Mapped[str] = mapped_column(String(16), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(1024))
    tender_req_candidate: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # content 永远保存 MinerU 原始文本；清洗只写入派生字段，避免过滤目录或
    # 合并残句后失去可审计的原文定位。
    cleaned_content: Mapped[str | None] = mapped_column(Text)
    cleaning_metadata: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    bbox: Mapped[object | None] = mapped_column(JSONB)
    metadata_: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderClause(Base):
    """由清洗后布局节点派生的可回溯业务条款，不等同于人工确认的需求。"""

    __tablename__ = "tender_clauses"
    __table_args__ = (UniqueConstraint("document_version_id", "order_no"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    clause_type: Mapped[str] = mapped_column(String(32), nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(1024))
    start_page: Mapped[int | None] = mapped_column(Integer)
    end_page: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    contextualized_content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mandatory_signal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_metadata: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClauseEvidence(Base):
    """条款到原始 Evidence 的派生关系，允许一条条款关联多个证据节点。"""

    __tablename__ = "clause_evidences"
    clause_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_clauses.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False, default="DERIVED_FROM")
