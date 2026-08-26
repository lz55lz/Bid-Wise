import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import ApiSchema
from app.schemas.documents import TaskResponse

KnowledgeType = Literal["LEGAL", "CASE"]
KnowledgeStatus = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]
FindingStatus = Literal["PENDING", "CONFIRMED", "RESOLVED", "FALSE_POSITIVE", "IGNORED"]
ChallengeStatus = Literal["DRAFT", "UNDER_REVIEW", "APPROVED", "REJECTED"]
QuoteStatus = Literal["DRAFT", "LOCKED", "ARCHIVED"]
WorkItemStatus = Literal["OPEN", "IN_PROGRESS", "DONE", "CANCELLED"]
MarketConclusion = Literal["SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"]
AgentWorkflow = Literal["BID_READINESS_REVIEW", "COMPLIANCE_REVIEW", "MARKET_REVIEW"]
IntegrationOperation = Literal["LOOKUP", "EXPORT"]


class KnowledgeCreateRequest(ApiSchema):
    knowledge_type: KnowledgeType
    title: str = Field(min_length=1, max_length=512)
    authority: str | None = Field(default=None, max_length=256)
    source_reference: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=100_000)
    issued_on: date | None = None
    effective_on: date | None = None
    citation_note: str | None = Field(default=None, max_length=4_000)


class KnowledgeRevisionRequest(ApiSchema):
    content: str = Field(min_length=1, max_length=100_000)
    issued_on: date | None = None
    effective_on: date | None = None
    citation_note: str | None = Field(default=None, max_length=4_000)


class KnowledgeResponse(ApiSchema):
    entry_id: UUID
    version_id: UUID
    version_no: int
    knowledge_type: KnowledgeType
    title: str
    authority: str | None
    source_reference: str
    status: KnowledgeStatus
    content: str
    issued_on: date | None
    effective_on: date | None
    citation_note: str | None
    source_document_version_id: UUID | None
    source_parse_status: str | None
    source_cleaning_summary: dict[str, Any] | None
    published_at: datetime | None
    created_at: datetime


class KnowledgeDocumentTaskResponse(ApiSchema):
    knowledge: KnowledgeResponse
    document_id: UUID
    document_version_id: UUID
    version_no: int
    task: TaskResponse


class KnowledgeQuestionRequest(ApiSchema):
    question: str = Field(min_length=1, max_length=2_000)
    project_id: UUID | None = None

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class KnowledgeCitation(ApiSchema):
    evidence_id: UUID
    document_id: UUID | None
    document_version_id: UUID | None
    file_name: str | None
    version_no: int | None
    page_number: int | None
    quoted_text: str | None
    scope: Literal["KNOWLEDGE", "PROJECT"]
    knowledge_entry_id: UUID | None = None
    knowledge_version_id: UUID | None = None
    knowledge_type: KnowledgeType | None = None
    knowledge_title: str | None = None
    source_reference: str | None = None


class KnowledgeQuestionResponse(ApiSchema):
    answer: str
    citations: list[KnowledgeCitation]
    no_evidence: bool


AnalysisMethod = Literal["DETERMINISTIC_RULES", "LLM_ANALYSIS", "HYBRID"]


class CompetitiveAnalysisCreate(ApiSchema):
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)
    requirement_id: UUID | None = None
    knowledge_version_ids: list[UUID] = Field(default_factory=list, max_length=20)
    method: AnalysisMethod = Field(default="DETERMINISTIC_RULES")


class CompetitiveFindingDraft(ApiSchema):
    """LLM 生成的候选发现（结构化输出），所有发现状态必须为 PENDING。"""

    category: Literal[
        "BRAND_OR_PARAMETER",
        "EXCESSIVE_QUALIFICATION",
        "GEOGRAPHIC_RESTRICTION",
        "UNIQUE_SUPPLY",
        "INCONSISTENT_REQUIREMENT",
        "OTHER",
    ]
    title: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=2000)
    confidence: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    evidence_indices: list[int] = Field(
        default_factory=list,
        description="引用的 evidence 在原始 evidence_ids 列表中的索引",
    )


class CompetitiveFindingResponse(ApiSchema):
    id: UUID
    category: str
    title: str
    description: str
    confidence: Decimal | None
    status: FindingStatus
    resolution: str | None
    evidence_ids: list[UUID]
    knowledge_version_ids: list[UUID]
    reviewed_by: UUID | None
    reviewed_at: datetime | None


class CompetitiveAnalysisResponse(ApiSchema):
    id: UUID
    project_id: UUID
    requirement_id: UUID | None
    status: str
    method: str
    summary: str | None
    evidence_ids: list[UUID]
    findings: list[CompetitiveFindingResponse]
    created_at: datetime
    completed_at: datetime | None


class CompetitiveFindingReviewRequest(ApiSchema):
    status: FindingStatus
    resolution: str | None = Field(default=None, max_length=2_000)
    knowledge_version_ids: list[UUID] = Field(default_factory=list, max_length=20)


class ChallengeDraftCreate(ApiSchema):
    title: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=10_000)
    fact_statement: str = Field(min_length=1, max_length=30_000)
    requested_action: str = Field(min_length=1, max_length=10_000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)


class ChallengeDraftReview(ApiSchema):
    status: Literal["UNDER_REVIEW", "APPROVED", "REJECTED"]
    review_note: str = Field(min_length=1, max_length=4_000)


class ChallengeDraftResponse(ApiSchema):
    id: UUID
    project_id: UUID
    title: str
    subject: str
    fact_statement: str
    requested_action: str
    status: ChallengeStatus
    review_note: str | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None
    evidence_ids: list[UUID]
    has_docx: bool
    has_pdf: bool
    created_at: datetime
    updated_at: datetime


class QuoteScenarioCreate(ApiSchema):
    name: str = Field(min_length=1, max_length=256)
    cost_excluding_tax: Decimal = Field(ge=0)
    tax_rate: Decimal = Field(ge=0, le=1)
    target_margin_rate: Decimal = Field(ge=0, lt=1)
    risk_adjustment: Decimal = Field(default=Decimal("0"))
    expected_score: Decimal | None = Field(default=None, ge=0)
    assumptions: dict[str, Any] = Field(default_factory=dict)


class QuoteScenarioResponse(ApiSchema):
    id: UUID
    project_id: UUID
    parent_scenario_id: UUID | None
    name: str
    version_no: int
    status: QuoteStatus
    cost_excluding_tax: Decimal
    tax_rate: Decimal
    target_margin_rate: Decimal
    risk_adjustment: Decimal
    expected_score: Decimal | None
    assumptions: dict[str, Any]
    calculations: dict[str, Any]
    locked_at: datetime | None
    created_at: datetime


class ProjectCommentCreate(ApiSchema):
    content: str = Field(min_length=1, max_length=10_000)
    target_type: str | None = Field(default=None, max_length=64)
    target_id: UUID | None = None


class ProjectCommentResponse(ApiSchema):
    id: UUID
    project_id: UUID
    content: str
    target_type: str | None
    target_id: UUID | None
    created_by: UUID
    created_at: datetime


class WorkItemCreate(ApiSchema):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=10_000)
    assignee_id: UUID | None = None
    due_at: datetime | None = None
    target_type: str | None = Field(default=None, max_length=64)
    target_id: UUID | None = None


class WorkItemUpdate(ApiSchema):
    status: WorkItemStatus
    closing_note: str | None = Field(default=None, max_length=4_000)


class WorkItemResponse(ApiSchema):
    id: UUID
    project_id: UUID
    title: str
    description: str | None
    status: WorkItemStatus
    assignee_id: UUID | None
    due_at: datetime | None
    target_type: str | None
    target_id: UUID | None
    closing_note: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class NotificationResponse(ApiSchema):
    id: UUID
    project_id: UUID | None
    notification_type: str
    payload: dict[str, Any]
    read_at: datetime | None
    created_at: datetime


class MarketCheckCreate(ApiSchema):
    requirement_id: UUID | None = None
    evidence_id: UUID | None = None
    parameter: str = Field(min_length=1, max_length=10_000)
    source_name: str = Field(min_length=1, max_length=256)
    source_reference: str = Field(min_length=1, max_length=1024)
    excerpt: str = Field(min_length=1, max_length=20_000)
    conclusion: MarketConclusion
    note: str | None = Field(default=None, max_length=4_000)


class MarketCheckResponse(ApiSchema):
    id: UUID
    project_id: UUID
    requirement_id: UUID | None
    evidence_id: UUID | None
    parameter: str
    source_name: str
    source_reference: str
    excerpt: str
    conclusion: MarketConclusion
    note: str | None
    created_at: datetime
    created_by: UUID


class GraphNodeResponse(ApiSchema):
    id: UUID
    entity_type: str
    source_object_id: str
    label: str
    attributes: dict[str, Any]
    source_evidence_id: UUID | None


class GraphEdgeResponse(ApiSchema):
    id: UUID
    from_node_id: UUID
    to_node_id: UUID
    relation_type: str
    source_evidence_id: UUID | None


class ProjectGraphResponse(ApiSchema):
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class AgentRunCreate(ApiSchema):
    workflow: AgentWorkflow
    goal: str = Field(min_length=1, max_length=10_000)
    document_version_id: UUID | None = None


class AgentRunStepResponse(ApiSchema):
    step_name: str
    status: str
    attempt: int
    output_summary: dict[str, Any]
    model_id: str | None
    input_hash: str | None
    output_hash: str | None
    latency_ms: int | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class AgentRunReview(ApiSchema):
    approved: bool
    note: str = Field(min_length=1, max_length=2_000)


class AgentRecommendationAdopt(ApiSchema):
    note: str = Field(min_length=1, max_length=2_000)


class AgentRecommendationResponse(ApiSchema):
    id: UUID
    kind: str
    source_agent: str
    title: str
    description: str
    risk_type: str | None
    severity: str | None
    priority: str | None
    owner_role: str | None
    status: str
    evidence_ids: list[UUID]
    adopted_target_type: str | None
    adopted_target_id: UUID | None
    review_note: str | None
    reviewed_by: UUID | None
    reviewed_at: datetime | None


class AgentRunResponse(ApiSchema):
    id: UUID
    project_id: UUID
    source_document_version_id: UUID | None
    workflow: AgentWorkflow
    status: str
    goal: str
    input_hash: str
    thread_id: str | None
    checkpoint_version: int
    requires_human_review: bool
    result: dict[str, Any]
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    evidence_ids: list[UUID]
    steps: list[AgentRunStepResponse] = Field(default_factory=list)
    recommendations: list[AgentRecommendationResponse] = Field(default_factory=list)


class ConnectorUpdate(ApiSchema):
    is_enabled: bool


class ConnectorResponse(ApiSchema):
    code: str
    name: str
    capabilities: list[str]
    is_enabled: bool
    is_configured: bool


class IntegrationRunCreate(ApiSchema):
    connector_code: Literal["ERP", "CRM", "PUBLIC_RESOURCE"]
    operation: IntegrationOperation
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def payload_must_be_small_json_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("连接器请求体必须为 JSON 对象") from exc
        if len(serialized.encode("utf-8")) > 32 * 1024:
            raise ValueError("连接器请求体不能超过 32KB")
        return value


class IntegrationRunResponse(ApiSchema):
    id: UUID
    project_id: UUID
    connector_code: str
    operation: IntegrationOperation
    status: str
    result_summary: dict[str, Any]
    external_reference: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
