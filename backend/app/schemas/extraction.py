from decimal import Decimal
from typing import Any, Literal

from pydantic import Field

from app.schemas.base import ApiSchema


class RequirementCandidate(ApiSchema):
    category: Literal["PROJECT", "QUALIFICATION", "BUSINESS", "SCORING"]
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=10_000)
    conditions: dict[str, Any] = Field(default_factory=dict)
    is_mandatory: bool = False
    score: Decimal | None = Field(default=None, ge=0)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    # 证据锚点：使用 order_no（文档行号）而非 node_id，便于 CoverageChecker 直接匹配
    evidence_order_nos: list[int] = Field(default_factory=list, max_length=20)


class ProjectFieldCandidate(ApiSchema):
    field_code: Literal[
        "PROJECT_NAME",
        "PROJECT_CODE",
        "PURCHASER",
        "AGENCY",
        "BUDGET",
        "MAX_PRICE",
        "BID_BOND",
        "BID_OPENING_AT",
        "BID_DEADLINE",
        "LOCATION",
        "PROCUREMENT_METHOD",
        "EVALUATION_METHOD",
    ]
    value_json: dict[str, Any] = Field(min_length=1)
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    # 证据锚点：使用 order_no（文档行号）而非 node_id
    evidence_order_nos: list[int] = Field(default_factory=list, max_length=20)


class RequirementExtractionResult(ApiSchema):
    project_fields: list[ProjectFieldCandidate] = Field(default_factory=list, max_length=12)
    requirements: list[RequirementCandidate] = Field(default_factory=list, max_length=100)
