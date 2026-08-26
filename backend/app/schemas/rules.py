from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema

RiskType = Literal[
    "QUALIFICATION",
    "COMPLIANCE",
    "FORMAT",
    "TIME",
    "FINANCIAL",
    "TECHNICAL",
    "BUSINESS",
    "DOCUMENT",
]
RiskSeverity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


class RuleCreateRequest(ApiSchema):
    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str = Field(min_length=1, max_length=256)
    risk_type: RiskType
    severity: RiskSeverity
    definition: dict[str, Any]
    is_enabled: bool = True


class RuleVersionRequest(ApiSchema):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    risk_type: RiskType | None = None
    severity: RiskSeverity
    definition: dict[str, Any]
    is_enabled: bool = True


class RuleVersionResponse(ApiSchema):
    id: UUID
    version_no: int
    severity: RiskSeverity
    definition: dict[str, Any]
    is_enabled: bool
    effective_at: datetime
    retired_at: datetime | None
    created_at: datetime
    created_by: UUID


class RuleResponse(ApiSchema):
    id: UUID
    code: str
    name: str
    risk_type: RiskType
    active_version: RuleVersionResponse | None
