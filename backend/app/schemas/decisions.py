from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from app.schemas.base import ApiSchema

DecisionSuggestion = Literal["RECOMMEND", "CAUTION", "HOLD", "REJECT"]


class DecisionResponse(ApiSchema):
    id: UUID
    project_id: UUID
    suggestion: DecisionSuggestion
    hard_constraint_result: dict[str, Any]
    reason: str
    missing_materials: list[dict[str, Any]]
    evidence_ids: list[UUID]
    created_at: datetime
    created_by: UUID
