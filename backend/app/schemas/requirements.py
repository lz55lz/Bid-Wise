from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class RequirementResponse(ApiSchema):
    id: UUID
    project_id: UUID
    category: str
    title: str
    description: str | None
    conditions: dict[str, Any]
    is_mandatory: bool
    score: Decimal | None
    confidence: Decimal | None
    review_status: str
    primary_evidence_id: UUID | None
    evidence_ids: list[UUID]
    reviewed_at: datetime | None
    review_note: str | None


class RequirementReview(ApiSchema):
    review_status: Literal["PENDING", "CONFIRMED", "REJECTED"]
    review_note: str | None = Field(default=None, max_length=2_000)


class RequirementBulkReview(ApiSchema):
    requirement_ids: list[UUID] = Field(min_length=1, max_length=20)
    review_status: Literal["CONFIRMED", "REJECTED"]
    review_note: str | None = Field(default=None, max_length=2_000)
