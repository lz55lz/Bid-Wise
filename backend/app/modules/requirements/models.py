# ruff: noqa: E501
"""可审核的项目字段和招标需求事实。"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectField(Base):
    __tablename__ = "project_fields"
    __table_args__ = (
        CheckConstraint(
            "review_status IN ('PENDING', 'CONFIRMED', 'REJECTED')",
            name="project_fields_review_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    field_code: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    value: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    primary_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("evidences.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    extraction_source: Mapped[str] = mapped_column(String(16), nullable=False, default="LLM")
    source_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    source_pipeline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_pipeline_runs.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderRequirement(Base):
    __tablename__ = "tender_requirements"
    __table_args__ = (
        CheckConstraint(
            "category IN ('PROJECT', 'QUALIFICATION', 'BUSINESS', 'SCORING')",
            name="tender_requirements_category_check",
        ),
        CheckConstraint(
            "review_status IN ('PENDING', 'CONFIRMED', 'REJECTED')",
            name="tender_requirements_review_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    conditions: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False)
    score: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    review_status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    primary_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("evidences.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    extraction_source: Mapped[str] = mapped_column(String(16), nullable=False, default="LLM")
    source_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_versions.id"), index=True
    )
    source_pipeline_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tender_pipeline_runs.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
