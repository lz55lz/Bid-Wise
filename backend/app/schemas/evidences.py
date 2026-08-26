from typing import Any
from uuid import UUID

from app.schemas.base import ApiSchema


class EvidenceResponse(ApiSchema):
    id: UUID
    source_type: str
    document_id: UUID
    document_version_id: UUID
    document_node_id: UUID
    file_name: str
    version_no: int
    page_number: int | None
    quoted_text: str | None
    content_hash: str | None
    bbox: dict[str, Any] | None
    # RAG 检索需要的权限上下文；knowledge chunks 为全局无需校验
    actor_id: UUID | None = None
    role_codes: set[str] | None = None
