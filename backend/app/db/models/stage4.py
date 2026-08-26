from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.models.requirements import REVIEW_STATUS

RISK_TYPE = ENUM(
    "QUALIFICATION",
    "COMPLIANCE",
    "FORMAT",
    "TIME",
    "FINANCIAL",
    "TECHNICAL",
    "BUSINESS",
    "DOCUMENT",
    name="risk_type",
    schema="app",
    create_type=False,
)
RISK_SEVERITY = ENUM(
    "CRITICAL",
    "HIGH",
    "MEDIUM",
    "LOW",
    "INFO",
    name="risk_severity",
    schema="app",
    create_type=False,
)
RISK_STATUS = ENUM(
    "PENDING",
    "CONFIRMED",
    "RESOLVED",
    "FALSE_POSITIVE",
    "IGNORED",
    name="risk_status",
    schema="app",
    create_type=False,
)
MATERIAL_TYPE = ENUM(
    "QUALIFICATION",
    "CERTIFICATE",
    "PROJECT_EXPERIENCE",
    "PERSONNEL",
    name="material_type",
    schema="app",
    create_type=False,
)
MATCH_STATUS = ENUM(
    "MATCHED",
    "MISSING",
    "UNCERTAIN",
    # 以下为历史兼容值（不再写入，保留用于读取旧数据）
    "PARTIAL",
    "EXPIRED",
    "UNKNOWN",
    "CONFLICT",
    name="match_status",
    schema="app",
    create_type=False,
)
DECISION_SUGGESTION = ENUM(
    "RECOMMEND",
    "CAUTION",
    "HOLD",
    "REJECT",
    name="decision_suggestion",
    schema="app",
    create_type=False,
)
FINAL_DECISION = ENUM("BID", "ABANDON", name="final_decision", schema="app", create_type=False)
REPORT_STATUS = ENUM(
    "QUEUED", "GENERATING", "READY", "FAILED", name="report_status", schema="app", create_type=False
)


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    risk_type: Mapped[str] = mapped_column(RISK_TYPE, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class RuleVersion(Base):
    __tablename__ = "rule_versions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    rule_id: Mapped[UUID] = mapped_column(ForeignKey("app.rules.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(RISK_SEVERITY, nullable=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class Risk(Base):
    __tablename__ = "risks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    rule_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.rule_versions.id"))
    risk_type: Mapped[str] = mapped_column(RISK_TYPE, nullable=False)
    severity: Mapped[str] = mapped_column(RISK_SEVERITY, nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(RISK_STATUS, nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    primary_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RiskEvidence(Base):
    __tablename__ = "risk_evidences"

    risk_id: Mapped[UUID] = mapped_column(ForeignKey("app.risks.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class RiskReview(Base):
    __tablename__ = "risk_reviews"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    risk_id: Mapped[UUID] = mapped_column(ForeignKey("app.risks.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(RISK_STATUS)
    to_status: Mapped[str] = mapped_column(RISK_STATUS, nullable=False)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EnterpriseMaterial(Base):
    __tablename__ = "enterprise_materials"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enterprise_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.enterprises.id"))
    material_type: Mapped[str] = mapped_column(MATERIAL_TYPE, nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    material_no: Mapped[str | None] = mapped_column(String(128))
    issuer: Mapped[str | None] = mapped_column(String(256))
    level: Mapped[str | None] = mapped_column(String(128))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(REVIEW_STATUS, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaterialDocument(Base):
    __tablename__ = "material_documents"

    material_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.enterprise_materials.id"), primary_key=True
    )
    document_id: Mapped[UUID] = mapped_column(ForeignKey("app.documents.id"), nullable=False)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.document_versions.id"), primary_key=True
    )
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MatchResult(Base):
    __tablename__ = "match_results"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    requirement_id: Mapped[UUID] = mapped_column(ForeignKey("app.requirements.id"), nullable=False)
    material_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.enterprise_materials.id"))
    automatic_status: Mapped[str] = mapped_column(MATCH_STATUS, nullable=False)
    final_status: Mapped[str] = mapped_column(MATCH_STATUS, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    missing_conditions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    is_overridden: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MatchEvidence(Base):
    __tablename__ = "match_evidences"

    match_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.match_results.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)
    side: Mapped[str] = mapped_column(String(16), primary_key=True)


class MatchOverride(Base):
    __tablename__ = "match_overrides"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    match_result_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.match_results.id"), nullable=False
    )
    previous_status: Mapped[str] = mapped_column(MATCH_STATUS, nullable=False)
    final_status: Mapped[str] = mapped_column(MATCH_STATUS, nullable=False)
    override_reason: Mapped[str] = mapped_column(Text, nullable=False)
    overridden_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    overridden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    suggestion: Mapped[str] = mapped_column(DECISION_SUGGESTION, nullable=False)
    hard_constraint_result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    missing_materials: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    final_decision: Mapped[str | None] = mapped_column(FINAL_DECISION)
    confirmed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class DecisionEvidence(Base):
    __tablename__ = "decision_evidences"

    decision_id: Mapped[UUID] = mapped_column(ForeignKey("app.decisions.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class Report(Base):
    __tablename__ = "reports"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    analysis_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.analysis_runs.id"))
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    report_type: Mapped[str] = mapped_column(String(20), nullable=False, default="SIMPLE")
    status: Mapped[str] = mapped_column(REPORT_STATUS, nullable=False)
    docx_object_key: Mapped[str | None] = mapped_column(String(1024))
    pdf_object_key: Mapped[str | None] = mapped_column(String(1024))
    md_object_key: Mapped[str | None] = mapped_column(String(1024))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    generated_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportSection(Base):
    __tablename__ = "report_sections"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    report_id: Mapped[UUID] = mapped_column(ForeignKey("app.reports.id"), nullable=False)
    section_code: Mapped[str] = mapped_column(String(64), nullable=False)
    order_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportEvidence(Base):
    __tablename__ = "report_evidences"

    report_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.report_sections.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)
