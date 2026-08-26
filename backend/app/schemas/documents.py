from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema


class TaskResponse(ApiSchema):
    id: UUID
    task_type: str
    target_type: str
    target_id: UUID
    status: str
    attempt: int
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class DocumentVersionResponse(ApiSchema):
    id: UUID
    version_no: int
    file_name: str
    file_size: int
    mime_type: str
    sha256: str
    parse_status: str
    error_code: str | None
    error_message: str | None
    cleaning_summary: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


class DocumentResponse(ApiSchema):
    id: UUID
    project_id: UUID | None
    document_type: Literal["TENDER", "ENTERPRISE", "LEGAL", "CASE"]
    logical_name: str
    current_version_id: UUID | None
    versions: list[DocumentVersionResponse]


class BidDocumentCard(ApiSchema):
    """文档卡片 - documents + document_versions 联合返回"""
    doc_id: UUID
    doc_name: str
    parse_status: str
    created_at: str | None  # ISO timestamp


class BidReportCard(ApiSchema):
    """报告卡片 - bid_report 简化返回"""
    doc_id: int
    decision: str
    overall_score: float
    summary: str
    report_md: str | None
    created_at: str | None


class DocumentTaskResponse(ApiSchema):
    document_id: UUID
    document_version_id: UUID
    version_no: int
    task: TaskResponse


class DocumentNodeResponse(ApiSchema):
    id: UUID
    document_version_id: UUID
    node_type: str
    page_number: int | None
    section_path: str | None
    order_no: int
    content: str
    content_hash: str
    cleaned_content: str | None
    cleaning_metadata: dict[str, Any]
    bbox: dict[str, Any] | None
    metadata: dict[str, Any]


class TenderClauseResponse(ApiSchema):
    id: UUID
    order_no: int
    clause_type: str
    section_path: str | None
    start_page: int | None
    end_page: int | None
    content: str
    mandatory_signal: bool
    evidence_ids: list[UUID]


class DocumentNodePage(ApiSchema):
    document_id: UUID
    document_version_id: UUID
    items: list[DocumentNodeResponse]
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
