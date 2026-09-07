"""对话 HTTP 契约。"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationCreateRequest(BaseModel):
    """新建会话；未填写标题时使用通用标题，避免模型替用户生成标题。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(default="项目问答", min_length=1, max_length=256)


class ConversationUpdateRequest(BaseModel):
    """会话标题可修改，消息内容保持不可变。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=256)


class ConversationResponse(BaseModel):
    """会话列表条目。"""

    model_config = ConfigDict(from_attributes=True)
    id: UUID
    project_id: UUID | None
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationMessageRequest(BaseModel):
    """用户问题；项目范围由 URL 和数据库会话决定。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=2_000)


class ConversationCitation(BaseModel):
    """助手本轮实际引用的项目 Evidence 或已发布法规知识摘要。"""

    source: str = Field(pattern="^(PROJECT_EVIDENCE|LEGAL_KNOWLEDGE|PROJECT_REPORT)$")
    evidence_id: UUID | None = None
    knowledge_chunk_id: UUID | None = None
    report_id: UUID | None = None
    quoted_text: str
    locator: dict[str, object]


class ConversationMessageResponse(BaseModel):
    """消息与可展示审计摘要。"""

    id: UUID
    role: str
    content: str
    citations: list[ConversationCitation]
    traces: list[dict[str, object]]
    created_at: datetime


class ConversationMessagePage(BaseModel):
    items: list[ConversationMessageResponse]
    total: int
    offset: int
    limit: int
