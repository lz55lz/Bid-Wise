"""Runtime helpers shared by the HTTP callback and ARQ worker."""

from __future__ import annotations

from app.core.errors import DomainError
from app.db.models.im_channel import IMChannel
from app.integrations.im.adapters import (
    REGION_FEISHU,
    REGION_LARK,
    FeishuAdapter,
    IMAdapter,
    WeComAdapter,
)


def serialize_channel(channel: IMChannel) -> dict:
    """Return the minimum channel configuration needed by an IM turn."""
    return {
        "id": channel.id,
        "platform": channel.platform,
        "credentials": channel.credentials,
        "project_id": channel.project_id,
        "owner_user_id": channel.owner_user_id,
        "output_mode": channel.output_mode,
        "session_mode": channel.session_mode,
        "knowledge_base_id": channel.knowledge_base_id,
        "agent_id": channel.agent_id,
    }


def build_adapter(channel_data: dict) -> IMAdapter:
    """Create an adapter from persisted channel configuration."""
    platform = channel_data.get("platform", "")
    credentials = channel_data.get("credentials", {})
    if platform == "wecom":
        return WeComAdapter(credentials)
    if platform in {"feishu", "lark"}:
        region = REGION_LARK if platform == "lark" else REGION_FEISHU
        return FeishuAdapter(credentials, region=region)
    raise DomainError("IM_UNSUPPORTED_PLATFORM", f"不支持的平台: {platform}", 400)
