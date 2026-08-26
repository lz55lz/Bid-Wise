from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentOutput(BaseModel):
    """Every model response is parsed and validated before it can be persisted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SpecialistName = Literal[
    "qualification",
    "commercial",
    "technical",
    "scoring",
    "schedule",
    "legal_knowledge",
]


class AgentFinding(AgentOutput):
    title: str = Field(min_length=1, max_length=180)
    severity: Severity
    conclusion: str = Field(min_length=1, max_length=2_000)
    recommended_action: str = Field(min_length=1, max_length=2_000)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=12)
    limitations: list[str] = Field(default_factory=list, max_length=6)


class SpecialistAssessment(AgentOutput):
    agent: SpecialistName
    summary: str = Field(min_length=1, max_length=3_000)
    findings: list[AgentFinding] = Field(default_factory=list, max_length=30)
    open_questions: list[str] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1)


class StrategyAction(AgentOutput):
    priority: Literal["P0", "P1", "P2"]
    action: str = Field(min_length=1, max_length=1_000)
    owner_role: str = Field(min_length=1, max_length=80)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=12)


class StrategyRecommendation(AgentOutput):
    bid_recommendation: Literal["PROCEED", "PROCEED_WITH_CONDITIONS", "HOLD"]
    rationale: str = Field(min_length=1, max_length=4_000)
    priority_actions: list[StrategyAction] = Field(default_factory=list, max_length=20)
    residual_risks: list[str] = Field(default_factory=list, max_length=15)
    confidence: float = Field(ge=0, le=1)


class EvidenceCritique(AgentOutput):
    requires_human_review: bool
    blockers: list[str] = Field(default_factory=list, max_length=20)
    unsupported_claims: list[str] = Field(default_factory=list, max_length=20)
    reviewer_focus: list[str] = Field(default_factory=list, max_length=12)
    retry_specialists: list[SpecialistName] = Field(default_factory=list, max_length=2)
    conclusion: str = Field(min_length=1, max_length=2_000)
