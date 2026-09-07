"""通用法规/案例知识库，不与项目私有 Evidence 混存。"""

from datetime import datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    authority: Mapped[str | None] = mapped_column(String(256))
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    issued_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    citation_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint("knowledge_entry_id", "version_no"),
        Index(
            "ux_knowledge_versions_published_entry",
            "knowledge_entry_id",
            unique=True,
            postgresql_where=text("status = 'PUBLISHED'"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_entries.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 与正文一起冻结的引用元数据。KnowledgeEntry 仅保留当前已发布摘要/逻辑类型，
    # 新 DRAFT 不能提前改变公共检索所展示的标题、来源或效力日期。
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    authority: Mapped[str | None] = mapped_column(String(256))
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    issued_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    citation_note: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)


class KnowledgeDocumentVersion(Base):
    """知识条目独立源文件版本，不复用项目文档的 project_id 授权模型。"""

    __tablename__ = "knowledge_document_versions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_versions.id"), nullable=False
    )
    original_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    parse_status: Mapped[str] = mapped_column(String(16), nullable=False, default="UPLOADED")
    parse_output_key: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    cleaning_summary: Mapped[dict[str, int] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeParseJob(Base):
    """公共知识源的解析任务；ARQ 只携带本表 ID，业务状态仍以 PostgreSQL 为准。"""

    __tablename__ = "knowledge_parse_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED')",
            name="knowledge_parse_jobs_status_check",
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_document_versions.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    arq_job_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_stage: Mapped[str] = mapped_column(String(32), nullable=False, default="QUEUED")
    progress_percent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    progress_message: Mapped[str | None] = mapped_column(String(256))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class KnowledgeChunk(Base):
    """可重建的公共知识向量块；查询时仍回查已发布版本，避免草稿泄漏。"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (UniqueConstraint("knowledge_version_id", "order_no"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_versions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(1024))
    embedding: Mapped[list[float]] = mapped_column(Vector(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
