# ruff: noqa: E501
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MaterialMatchResult(Base):
    __tablename__ = "material_match_results"
    __table_args__ = (
        CheckConstraint(
            "rule_status IN ('MATCHED', 'UNCERTAIN', 'MISSING')",
            name="material_match_results_rule_status_check",
        ),
        CheckConstraint(
            "final_status IN ('MATCHED', 'UNCERTAIN', 'MISSING')",
            name="material_match_results_final_status_check",
        ),
        UniqueConstraint("project_id", "requirement_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    requirement_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_requirements.id"), nullable=False
    )
    material_id: Mapped[UUID | None] = mapped_column(ForeignKey("enterprise_materials.id"))
    rule_status: Mapped[str] = mapped_column(String(16), nullable=False)
    final_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    missing_conditions: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    matched_facts: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    overridden_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))
    override_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
