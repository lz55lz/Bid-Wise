"""IM 渠道 CRUD 服务，对齐 WeKnora internal/im/service.go。"""

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db.models.im_channel import IMChannel

logger = logging.getLogger(__name__)

_VALID_PLATFORMS = {
    "wecom", "feishu", "lark", "slack", "telegram",
    "dingtalk", "mattermost", "wechat", "qqbot", "yunzhijia",
}


def _credentials_configured(credentials: dict[str, Any]) -> bool:
    s = str(credentials).strip()
    return s != "" and s != "{}"


def _compute_bot_identity(platform: str, mode: str, credentials: dict[str, Any]) -> str:
    """计算机器人身份标识，去重判断用。对齐 WeKnora computeBotIdentity。"""
    if platform == "wecom":
        if mode == "webhook":
            corp_id = str(credentials.get("corp_id", ""))
            agent_id = str(credentials.get("corp_agent_id", ""))
            if corp_id and agent_id:
                return f"wecom:wh:{corp_id}:{agent_id}"
        bot_id = str(credentials.get("bot_id", ""))
        if bot_id:
            return f"wecom:ws:{bot_id}"
    elif platform in ("feishu", "lark"):
        app_id = str(credentials.get("app_id", ""))
        if app_id:
            return f"{platform}:{app_id}"
    elif platform == "telegram":
        bot_token = str(credentials.get("bot_token", ""))
        if ":" in bot_token:
            return f"telegram:{bot_token.split(':')[0]}"
        return f"telegram:{bot_token}"
    elif platform == "dingtalk":
        client_id = str(credentials.get("client_id", ""))
        if client_id:
            return f"dingtalk:{client_id}"
    return ""


class IMChannelService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # ── 列表 ──────────────────────────────────────────────────────────────

    def list_by_agent(self, agent_id: str, owner_user_id: str) -> list[IMChannel]:
        stmt = select(IMChannel).where(
            IMChannel.agent_id == agent_id,
            IMChannel.owner_user_id == owner_user_id,
            IMChannel.deleted_at.is_(None),
        )
        return list(self._session.execute(stmt).scalars().all())

    def list_by_owner(self, owner_user_id: str) -> list[IMChannel]:
        stmt = select(IMChannel).where(
            IMChannel.owner_user_id == owner_user_id,
            IMChannel.deleted_at.is_(None),
        )
        return list(self._session.execute(stmt).scalars().all())

    # ── 查询 ──────────────────────────────────────────────────────────────

    def get_by_id_and_owner(self, channel_id: str, owner_user_id: str) -> IMChannel | None:
        stmt = select(IMChannel).where(
            IMChannel.id == channel_id,
            IMChannel.owner_user_id == owner_user_id,
            IMChannel.deleted_at.is_(None),
        )
        return self._session.execute(stmt).scalar_one_or_none()

    # ── 创建 ──────────────────────────────────────────────────────────────

    def create(
        self,
        owner_user_id: str,
        platform: str,
        name: str = "",
        mode: str = "",
        output_mode: str = "",
        session_mode: str = "user",
        knowledge_base_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        credentials: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> IMChannel:
        if platform not in _VALID_PLATFORMS:
            raise DomainError(
                "IM_INVALID_PLATFORM",
                f"platform 必须是以下之一: {', '.join(sorted(_VALID_PLATFORMS))}",
                400,
            )

        creds = credentials or {}

        if not mode:
            if platform in ("mattermost", "yunzhijia"):
                mode = "webhook"
            elif platform == "wechat":
                mode = "longpoll"
            else:
                mode = "webhook"

        if not output_mode:
            output_mode = "full"

        bot_identity = _compute_bot_identity(platform, mode, creds)

        if bot_identity:
            existing = self._session.execute(
                select(IMChannel).where(
                    IMChannel.bot_identity == bot_identity,
                    IMChannel.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if existing:
                msg = (
                    f"机器人身份冲突：{bot_identity}，"
                    f"该机器人已被渠道 {existing.name}（{existing.id}）占用"
                )
                raise DomainError("IM_DUPLICATE_BOT", msg, 409)

        channel = IMChannel(
            owner_user_id=owner_user_id,
            platform=platform,
            name=name or f"{platform} 渠道",
            mode=mode,
            output_mode=output_mode,
            session_mode=session_mode,
            knowledge_base_id=knowledge_base_id,
            agent_id=agent_id,
            project_id=project_id,
            credentials=creds,
            enabled=enabled,
            bot_identity=bot_identity,
        )
        self._session.add(channel)
        try:
            self._session.flush()
        except IntegrityError as err:
            self._session.rollback()
            raise DomainError("IM_CREATE_FAILED", "创建渠道失败，可能存在冲突", 500) from err
        return channel

    # ── 更新 ──────────────────────────────────────────────────────────────

    def update(
        self,
        channel: IMChannel,
        name: str | None = None,
        mode: str | None = None,
        output_mode: str | None = None,
        session_mode: str | None = None,
        knowledge_base_id: str | None = None,
        agent_id: str | None = None,
        project_id: str | None = None,
        credentials: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> IMChannel:
        if name is not None:
            channel.name = name
        if mode is not None:
            channel.mode = mode
        if output_mode is not None:
            channel.output_mode = output_mode
        if session_mode is not None:
            channel.session_mode = session_mode
        if knowledge_base_id is not None:
            channel.knowledge_base_id = knowledge_base_id
        if agent_id is not None:
            channel.agent_id = agent_id
        if project_id is not None:
            channel.project_id = project_id
        if credentials is not None:
            channel.credentials = credentials
            new_identity = _compute_bot_identity(channel.platform, channel.mode, credentials)
            channel.bot_identity = new_identity
        if enabled is not None:
            channel.enabled = enabled

        self._session.flush()
        return channel

    # ── 删除 ──────────────────────────────────────────────────────────────

    def soft_delete(self, channel: IMChannel) -> None:
        from datetime import UTC, datetime
        channel.deleted_at = datetime.now(UTC)
        self._session.flush()

    # ── 启停 ──────────────────────────────────────────────────────────────

    def toggle(self, channel: IMChannel) -> IMChannel:
        channel.enabled = not channel.enabled
        self._session.flush()
        return channel
