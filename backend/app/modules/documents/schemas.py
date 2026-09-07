"""项目文档 API 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DocumentVersionResponse(BaseModel):
    """当前文件版本的安全元数据；不返回对象键或预签名地址。"""

    id: UUID
    version_no: int
    original_file_name: str
    file_size: int
    mime_type: str
    sha256: str
    parse_status: str
    progress_percent: int | None = None
    progress_message: str | None = None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class ProjectDocumentResponse(BaseModel):
    """项目文件卡片；下载链接必须由后续授权下载接口按需生成。"""

    id: UUID
    project_id: UUID
    logical_name: str
    current_version: DocumentVersionResponse
    created_at: datetime


class DocumentParseJobResponse(BaseModel):
    """解析任务状态；前端以数据库状态为准，不直接查询 Redis。"""

    id: UUID
    document_version_id: UUID
    status: str
    attempt: int
    progress_stage: str
    progress_percent: int
    progress_message: str | None
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime


class DocumentNodeResponse(BaseModel):
    """已解析原文节点；仅项目成员可读取，用于人工核对提取和 Evidence。"""

    id: UUID
    node_type: str
    page_number: int | None
    section_path: str | None
    order_no: int
    content: str
    metadata: dict[str, object]


class DocumentNodePage(BaseModel):
    """节点分页结果，避免一次返回整份标书文本。"""

    items: list[DocumentNodeResponse]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1)
