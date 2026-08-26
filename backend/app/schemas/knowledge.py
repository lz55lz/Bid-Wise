from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.schemas.base import ApiSchema
from app.schemas.documents import TaskResponse

KnowledgeType = Literal["LEGAL", "CASE"]
KnowledgeStatus = Literal["DRAFT", "PUBLISHED", "ARCHIVED"]


class KnowledgeCreateRequest(ApiSchema):
    knowledge_type: KnowledgeType
    title: str = Field(min_length=1, max_length=512)
    authority: str | None = Field(default=None, max_length=256)
    source_reference: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=1, max_length=100_000)
    issued_on: date | None = None
    effective_on: date | None = None
    citation_note: str | None = Field(default=None, max_length=4_000)


class KnowledgeRevisionRequest(ApiSchema):
    content: str = Field(min_length=1, max_length=100_000)
    issued_on: date | None = None
    effective_on: date | None = None
    citation_note: str | None = Field(default=None, max_length=4_000)


class KnowledgeResponse(ApiSchema):
    entry_id: UUID
    version_id: UUID
    version_no: int
    knowledge_type: KnowledgeType
    title: str
    authority: str | None
    source_reference: str
    status: KnowledgeStatus
    content: str
    issued_on: date | None
    effective_on: date | None
    citation_note: str | None
    source_document_version_id: UUID | None
    source_parse_status: str | None
    source_cleaning_summary: dict[str, Any] | None
    published_at: datetime | None
    created_at: datetime


class KnowledgeDocumentTaskResponse(ApiSchema):
    knowledge: KnowledgeResponse
    document_id: UUID
    document_version_id: UUID
    version_no: int
    task: TaskResponse
