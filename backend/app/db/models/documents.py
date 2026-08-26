from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DOCUMENT_TYPE = ENUM(
    "TENDER", "ENTERPRISE", "LEGAL", "CASE", name="document_type", schema="app", create_type=False
)
PARSE_STATUS = ENUM(
    "UPLOADED",
    "QUEUED",
    "PARSING",
    "CLEANING",
    "PARSED",
    "STRUCTURING",
    "INDEXING",
    "READY",
    "FAILED",
    "RUNNING",
    "SUCCEEDED",
    name="parse_status",
    schema="app",
    create_type=False,
)
DOCUMENT_NODE_TYPE = ENUM(
    "SECTION",
    "PARAGRAPH",
    "TABLE",
    "CELL",
    "IMAGE",
    "LIST",
    name="document_node_type",
    schema="app",
    create_type=False,
)
EVIDENCE_SOURCE_TYPE = ENUM(
    "DOCUMENT_TEXT",
    "DOCUMENT_TABLE",
    "DOCUMENT_IMAGE",
    "DOCUMENT_SECTION",
    "USER_CONFIRMATION",
    "SYSTEM_RULE",
    name="evidence_source_type",
    schema="app",
    create_type=False,
)
TASK_TYPE = ENUM(
    "PARSE_DOCUMENT",
    "CLEAN_DOCUMENT",
    "EXTRACT_REQUIREMENTS",
    "INDEX_DOCUMENT",
    "RUN_RISK_CHECK",
    "RUN_MATCH",
    "GENERATE_DECISION",
    "GENERATE_REPORT",
    "RAG_ANSWER",
    "RUN_BID_READINESS_AGENT",
    "RUN_PROJECT_ANALYSIS",
    "PIPELINE_DOCUMENT",
    name="task_type",
    schema="app",
    create_type=False,
)
TASK_STATUS = ENUM(
    "QUEUED",
    "RUNNING",
    "WAITING_HUMAN_REVIEW",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    name="task_status",
    schema="app",
    create_type=False,
)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tender_projects.id"))
    document_type: Mapped[str] = mapped_column(DOCUMENT_TYPE)
    logical_name: Mapped[str] = mapped_column(String(512))
    current_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.document_versions.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(ForeignKey("app.documents.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(PARSE_STATUS, nullable=False)
    parse_output_key: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    cleaning_summary: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    pipeline_thread_id: Mapped[str | None] = mapped_column(String(128))


class DocumentNode(Base):
    __tablename__ = "document_nodes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.document_versions.id"), nullable=False
    )
    parent_node_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.document_nodes.id"))
    node_type: Mapped[str] = mapped_column(DOCUMENT_NODE_TYPE, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(String(1024))
    tender_req_candidate: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    cleaned_content: Mapped[str | None] = mapped_column(Text)
    cleaning_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Evidence(Base):
    __tablename__ = "evidences"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_type: Mapped[str] = mapped_column(EVIDENCE_SOURCE_TYPE, nullable=False)
    document_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.document_versions.id"))
    document_node_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.document_nodes.id"))
    page_number: Mapped[int | None] = mapped_column(Integer)
    quoted_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    bbox: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    source_reference: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))


class TenderClause(Base):
    """A business-readable tender clause assembled from layout-preserving nodes."""

    __tablename__ = "tender_clauses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.document_versions.id"), nullable=False
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
    quality_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClauseEvidence(Base):
    __tablename__ = "clause_evidences"

    clause_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.tender_clauses.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_type: Mapped[str] = mapped_column(TASK_TYPE, nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[UUID] = mapped_column(nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(TASK_STATUS, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_task_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tasks.id"))
    celery_task_id: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[UUID] = mapped_column(ForeignKey("app.tasks.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(TASK_STATUS)
    to_status: Mapped[str] = mapped_column(TASK_STATUS, nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
