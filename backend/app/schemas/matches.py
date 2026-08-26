from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema

MatchStatus = Literal[
    "MATCHED", "MISSING", "UNCERTAIN",
    "PARTIAL", "EXPIRED", "UNKNOWN", "CONFLICT",
]


class MatchOverrideRequest(ApiSchema):
    final_status: MatchStatus
    reason: str = Field(min_length=1, max_length=2_000)


class MatchResponse(ApiSchema):
    id: UUID
    project_id: UUID
    requirement_id: UUID
    material_id: UUID | None
    automatic_status: MatchStatus
    final_status: MatchStatus
    reason: str
    missing_conditions: list[dict[str, Any]]
    is_overridden: bool
    evidence_ids: list[UUID]
    created_at: datetime
    updated_at: datetime
