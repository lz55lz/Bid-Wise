from datetime import datetime
from typing import Literal
from uuid import UUID

from app.schemas.base import ApiSchema

ReportStatus = Literal["QUEUED", "GENERATING", "READY", "FAILED"]
ReportType = Literal["SIMPLE", "FULL"]


class ReportSectionResponse(ApiSchema):
    section_code: str
    order_no: int
    content_markdown: str
    evidence_ids: list[UUID]


class ReportResponse(ApiSchema):
    id: UUID
    project_id: UUID
    version_no: int
    status: ReportStatus
    error_code: str | None
    error_message: str | None
    generated_by: UUID
    generated_at: datetime | None
    created_at: datetime
    sections: list[ReportSectionResponse]
