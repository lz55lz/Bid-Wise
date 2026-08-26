from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    authority: Mapped[str | None] = mapped_column(String(256))
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeVersion(Base):
    __tablename__ = "knowledge_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    knowledge_entry_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.knowledge_entries.id"), nullable=False
    )
    source_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.document_versions.id")
    )
    source_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    issued_on: Mapped[date | None] = mapped_column(Date)
    effective_on: Mapped[date | None] = mapped_column(Date)
    citation_note: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
