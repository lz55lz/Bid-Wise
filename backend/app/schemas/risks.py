from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema

RiskStatus = Literal["PENDING", "CONFIRMED", "RESOLVED", "FALSE_POSITIVE", "IGNORED"]


class RiskReviewRequest(ApiSchema):
    status: RiskStatus
    resolution: str | None = Field(default=None, max_length=2_000)


class RiskResponse(ApiSchema):
    id: UUID
    project_id: UUID
    rule_version_id: UUID | None
    risk_type: str
    severity: str
    title: str
    description: str
    trigger_data: dict[str, Any]
    confidence: Decimal | None
    status: RiskStatus
    resolution: str | None
    primary_evidence_id: UUID | None
    evidence_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
