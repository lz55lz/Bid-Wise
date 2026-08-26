from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

REVIEW_STATUS = ENUM(
    "PENDING", "CONFIRMED", "REJECTED", "DEFERRED",
    name="review_status", schema="app", create_type=False
)
REQUIREMENT_CATEGORY = ENUM(
    "PROJECT",
    "QUALIFICATION",
    "BUSINESS",
    "SCORING",
    name="requirement_category",
    schema="app",
    create_type=False,
)


class ProjectField(Base):
    __tablename__ = "project_fields"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    field_code: Mapped[str] = mapped_column(String(80), nullable=False)
    value_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(REVIEW_STATUS, nullable=False)
    primary_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    extraction_source: Mapped[str | None] = mapped_column(String(16), server_default="llm")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Requirement(Base):
    __tablename__ = "requirements"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    category: Mapped[str] = mapped_column(REQUIREMENT_CATEGORY, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(REVIEW_STATUS, nullable=False)
    primary_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    extraction_source: Mapped[str | None] = mapped_column(String(16), server_default="llm")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RequirementEvidence(Base):
    __tablename__ = "requirement_evidences"
    requirement_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.requirements.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
