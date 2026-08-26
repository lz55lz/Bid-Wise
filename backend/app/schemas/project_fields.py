from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class ProjectFieldResponse(ApiSchema):
    id: UUID
    project_id: UUID
    field_code: str
    value_json: dict[str, Any]
    confidence: Decimal | None
    review_status: str
    primary_evidence_id: UUID | None
    reviewed_at: datetime | None
    review_note: str | None


class ProjectFieldReview(ApiSchema):
    review_status: Literal["PENDING", "CONFIRMED", "REJECTED"]
    review_note: str | None = Field(default=None, max_length=2_000)
