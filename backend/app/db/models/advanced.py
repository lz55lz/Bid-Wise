from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AnalysisRun(Base):
    """Durable, project-level orchestration envelope for one bid analysis."""

    __tablename__ = "analysis_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    current_stage: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tasks.id"))
    report_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.reports.id"))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class AnalysisSnapshot(Base):
    """Immutable input and stage-output manifest for an analysis run."""

    __tablename__ = "analysis_snapshots"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.analysis_runs.id"), nullable=False, unique=True
    )
    tender_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    enterprise_material_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    rule_version_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    stage_outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompetitiveAnalysis(Base):
    __tablename__ = "competitive_analyses"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.requirements.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CompetitiveAnalysisEvidence(Base):
    __tablename__ = "competitive_analysis_evidences"

    analysis_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.competitive_analyses.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class CompetitiveFinding(Base):
    __tablename__ = "competitive_findings"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    analysis_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.competitive_analyses.id"), nullable=False
    )
    category: Mapped[str] = mapped_column(String(48), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    resolution: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CompetitiveFindingEvidence(Base):
    __tablename__ = "competitive_finding_evidences"

    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.competitive_findings.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class CompetitiveFindingKnowledge(Base):
    __tablename__ = "competitive_finding_knowledge"

    finding_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.competitive_findings.id"), primary_key=True
    )
    knowledge_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.knowledge_versions.id"), primary_key=True
    )


class ChallengeDraft(Base):
    __tablename__ = "challenge_drafts"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)
    fact_statement: Mapped[str] = mapped_column(Text, nullable=False)
    requested_action: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    docx_object_key: Mapped[str | None] = mapped_column(String(1024))
    pdf_object_key: Mapped[str | None] = mapped_column(String(1024))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ChallengeDraftEvidence(Base):
    __tablename__ = "challenge_draft_evidences"

    challenge_draft_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.challenge_drafts.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class QuoteScenario(Base):
    __tablename__ = "quote_scenarios"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    parent_scenario_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.quote_scenarios.id"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    cost_excluding_tax: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    target_margin_rate: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    risk_adjustment: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    expected_score: Mapped[Decimal | None] = mapped_column(Numeric(9, 4))
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    calculations: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectComment(Base):
    __tablename__ = "project_comments"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[UUID | None] = mapped_column()
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class WorkItem(Base):
    __tablename__ = "work_items"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    assignee_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[UUID | None] = mapped_column()
    closing_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tender_projects.id"))
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MarketCheck(Base):
    __tablename__ = "market_checks"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    requirement_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.requirements.id"))
    evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    parameter: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(1024), nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    conclusion: Mapped[str] = mapped_column(String(24), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class GraphNode(Base):
    __tablename__ = "graph_nodes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_object_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(512), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GraphEdge(Base):
    __tablename__ = "graph_edges"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    from_node_id: Mapped[UUID] = mapped_column(ForeignKey("app.graph_nodes.id"), nullable=False)
    to_node_id: Mapped[UUID] = mapped_column(ForeignKey("app.graph_nodes.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_evidence_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.evidences.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    source_document_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("app.document_versions.id")
    )
    workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(128))
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    requires_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class AgentRunEvidence(Base):
    __tablename__ = "agent_run_evidences"

    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("app.agent_runs.id"), primary_key=True)
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class AgentRunStep(Base):
    __tablename__ = "agent_run_steps"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("app.agent_runs.id"), nullable=False)
    step_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    output_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    model_id: Mapped[str | None] = mapped_column(String(32))
    input_hash: Mapped[str | None] = mapped_column(String(64))
    output_hash: Mapped[str | None] = mapped_column(String(64))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRecommendation(Base):
    __tablename__ = "agent_recommendations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    agent_run_id: Mapped[UUID] = mapped_column(ForeignKey("app.agent_runs.id"), nullable=False)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    source_agent: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    risk_type: Mapped[str | None] = mapped_column(String(32))
    severity: Mapped[str | None] = mapped_column(String(16))
    priority: Mapped[str | None] = mapped_column(String(8))
    owner_role: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    adopted_target_type: Mapped[str | None] = mapped_column(String(64))
    adopted_target_id: Mapped[UUID | None] = mapped_column()
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AgentRecommendationEvidence(Base):
    __tablename__ = "agent_recommendation_evidences"

    recommendation_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.agent_recommendations.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(ForeignKey("app.evidences.id"), primary_key=True)


class IntegrationConnector(Base):
    __tablename__ = "integration_connectors"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IntegrationRun(Base):
    __tablename__ = "integration_runs"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), nullable=False)
    connector_code: Mapped[str] = mapped_column(
        ForeignKey("app.integration_connectors.code"), nullable=False
    )
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_summary: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    external_reference: Mapped[str | None] = mapped_column(String(256))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
