from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

try:
    from pgvector.sqlalchemy import Vector as PgVector
except ImportError:
    PgVector = None  # type: ignore[assignment,misc]

AI_RUN_STATUS = ENUM(
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "VALIDATION_FAILED",
    name="ai_run_status",
    schema="app",
    create_type=False,
)


class AiRun(Base):
    __tablename__ = "ai_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tasks.id"))
    scene: Mapped[str] = mapped_column(String(80), nullable=False)
    model_id: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(AI_RUN_STATUS, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AiRunEvidence(Base):
    __tablename__ = "ai_run_evidences"

    ai_run_id: Mapped[UUID] = mapped_column(ForeignKey("app.ai_runs.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class SearchChunk(Base):
    __tablename__ = "search_chunks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    source_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.document_versions.id")
    )
    source_node_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.document_nodes.id"))
    evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tender_projects.id"))
    chunk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(PgVector(1024), nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column("metadata", JSONB, nullable=False)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 邻居块链接（用于短上下文扩展）
    pre_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.search_chunks.id", ondelete="SET NULL"), nullable=True
    )
    next_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.search_chunks.id", ondelete="SET NULL"), nullable=True
    )
    # WeKnora 风格内容类型（text/parent_text/faq/summary/image_ocr/image_caption）
    content_type: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    # 父块 ID（用于父子块体系）
    parent_chunk_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.search_chunks.id", ondelete="SET NULL"), nullable=True
    )
    # 原文 rune 偏移（用于高亮和内容还原）
    start_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # FAQ 元数据（标准问题/答案/相似问）
    faq_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB, nullable=True)
