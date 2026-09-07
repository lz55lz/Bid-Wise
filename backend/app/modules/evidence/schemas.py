"""Evidence 的受控输出模型。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class EvidenceResponse(BaseModel):
    """定位原文所需的最小证据数据，不暴露对象存储内部键。"""

    id: UUID
    project_id: UUID
    source_type: str
    document_version_id: UUID | None
    document_node_id: UUID | None
    quoted_text: str | None
    locator: dict[str, object]
    created_at: datetime
