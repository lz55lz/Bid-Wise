"""IM 集成 Pydantic schemas。"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Platform(StrEnum):
    """支持的 IM 平台。"""

    TELEGRAM = "telegram"
    WECOM = "wecom"
    FEISHU = "feishu"
    DINGTALK = "dingtalk"
    SLACK = "slack"
    LARK = "lark"
    MATTERMOST = "mattermost"
    WECHAT = "wechat"
    QQBOT = "qqbot"
    YUNZHIJIA = "yunzhijia"


class ChatType(StrEnum):
    """聊天类型。"""

    DIRECT = "direct"
    GROUP = "group"


class MessageType(StrEnum):
    """消息类型。"""

    TEXT = "text"
    FILE = "file"
    IMAGE = "image"


class IncomingMessage(BaseModel):
    """IM 平台回调解析后的统一入站消息。"""

    platform: Platform
    message_type: MessageType
    user_id: str
    chat_id: str
    content: str
    message_id: str
    chat_type: ChatType = ChatType.DIRECT
    thread_id: str | None = None
    file_key: str | None = None
    file_name: str | None = None
    file_size: int = 0
    quote: dict[str, Any] | None = None
    raw_payload: dict[str, Any] | None = Field(default=None, exclude=True)


class ReplyMessage(BaseModel):
    """统一出站回复消息。"""

    content: str
    # 流式模式暂未实现，当前始终发送完整消息
    is_stream: bool = False
    stream_message_id: str | None = None


class IMChannelCreate(BaseModel):
    """创建 IM 渠道请求。"""

    platform: Platform
    name: str = ""
    mode: str = ""
    output_mode: str = ""
    session_mode: str = "user"
    knowledge_base_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    credentials: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class IMChannelUpdate(BaseModel):
    """更新 IM 渠道请求（所有字段可选）。"""

    name: str | None = None
    mode: str | None = None
    output_mode: str | None = None
    session_mode: str | None = None
    knowledge_base_id: str | None = None
    agent_id: str | None = None
    project_id: str | None = None
    credentials: dict[str, Any] | None = None
    enabled: bool | None = None


class IMChannelSummary(BaseModel):
    """IM 渠道列表项（不暴露凭据），对齐 WeKnora IMChannelSummary。"""

    id: str
    owner_user_id: str
    agent_id: str
    platform: Platform
    name: str
    enabled: bool
    mode: str
    output_mode: str
    session_mode: str
    knowledge_base_id: str | None = None
    bot_identity: str | None = None
    credentials_configured: bool
    created_at: str
    updated_at: str


class CommandResult(BaseModel):
    """斜杠命令执行结果。"""

    action: str  # reply / clear / stop / none
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
