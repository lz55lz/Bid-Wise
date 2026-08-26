"""IM 渠道管理 API，对齐 WeKnora /api/v1/im-channels。"""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.errors import DomainError
from app.db.models.im_channel import IMChannel
from app.db.session import get_db_session
from app.integrations.im.channel_service import IMChannelService
from app.integrations.im.schemas import (
    IMChannelCreate,
    IMChannelSummary,
    IMChannelUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/im/channels", tags=["IM"])


def _to_summary(channel: IMChannel) -> IMChannelSummary:
    """将 IMChannel ORM 模型转换为列表响应 Schema。"""
    from app.integrations.im.channel_service import _credentials_configured

    creds = channel.credentials or {}
    return IMChannelSummary(
        id=channel.id,
        owner_user_id=channel.owner_user_id,
        agent_id=channel.agent_id or "",
        platform=channel.platform,  # type: ignore[arg-type]
        name=channel.name,
        enabled=channel.enabled,
        mode=channel.mode,
        output_mode=channel.output_mode,
        session_mode=channel.session_mode,
        knowledge_base_id=channel.knowledge_base_id,
        bot_identity=channel.bot_identity or None,
        credentials_configured=_credentials_configured(creds),
        created_at=channel.created_at.isoformat() if channel.created_at else "",
        updated_at=channel.updated_at.isoformat() if channel.updated_at else "",
    )


# ── Owner channel list (no credentials) ──────────────────────────────────────

@router.get("", response_model=dict)
async def list_all_channels(
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """列出当前用户创建的 IM 渠道，不返回凭据。"""
    svc = IMChannelService(db)
    channels = svc.list_by_owner(str(current_user.id))
    return {"data": [_to_summary(ch) for ch in channels]}


# ── Per-agent channel list ────────────────────────────────────────────────────

@router.get("/by-agent/{agent_id}", response_model=dict)
async def list_agent_channels(
    agent_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """列出指定 Agent 下的 IM 渠道。"""
    svc = IMChannelService(db)
    channels = svc.list_by_agent(agent_id, str(current_user.id))
    return {"data": [_to_summary(ch) for ch in channels]}


# ── Get single channel ────────────────────────────────────────────────────────

@router.get("/{channel_id}", response_model=dict)
async def get_channel(
    channel_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """获取单个 IM 渠道详情。"""
    svc = IMChannelService(db)
    channel = svc.get_by_id_and_owner(channel_id, str(current_user.id))
    if channel is None:
        raise DomainError("IM_CHANNEL_NOT_FOUND", "渠道不存在", 404)
    return {"data": _to_summary(channel)}


# ── Create channel ────────────────────────────────────────────────────────────

@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: IMChannelCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """创建新的 IM 渠道。"""
    svc = IMChannelService(db)
    try:
        channel = svc.create(
            owner_user_id=str(current_user.id),
            platform=body.platform.value,
            name=body.name,
            mode=body.mode,
            output_mode=body.output_mode,
            session_mode=body.session_mode,
            knowledge_base_id=body.knowledge_base_id,
            agent_id=body.agent_id,
            project_id=body.project_id,
            credentials=body.credentials,
            enabled=body.enabled,
        )
        db.commit()
        return {"data": _to_summary(channel)}
    except DomainError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("[IM] create channel failed: %s", exc)
        raise DomainError("IM_CREATE_FAILED", "创建渠道失败", 500) from exc


# ── Update channel ────────────────────────────────────────────────────────────

@router.patch("/{channel_id}", response_model=dict)
async def update_channel(
    channel_id: str,
    body: IMChannelUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """更新 IM 渠道（所有字段可选）。"""
    svc = IMChannelService(db)
    channel = svc.get_by_id_and_owner(channel_id, str(current_user.id))
    if channel is None:
        raise DomainError("IM_CHANNEL_NOT_FOUND", "渠道不存在", 404)

    try:
        updated = svc.update(
            channel,
            name=body.name,
            mode=body.mode,
            output_mode=body.output_mode,
            session_mode=body.session_mode,
            knowledge_base_id=body.knowledge_base_id,
            agent_id=body.agent_id,
            project_id=body.project_id,
            credentials=body.credentials,
            enabled=body.enabled,
        )
        db.commit()
        return {"data": _to_summary(updated)}
    except DomainError:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("[IM] update channel failed: %s", exc)
        raise DomainError("IM_UPDATE_FAILED", "更新渠道失败", 500) from exc


# ── Delete channel ────────────────────────────────────────────────────────────

@router.delete("/{channel_id}", response_model=dict)
async def delete_channel(
    channel_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """软删除 IM 渠道。"""
    svc = IMChannelService(db)
    channel = svc.get_by_id_and_owner(channel_id, str(current_user.id))
    if channel is None:
        raise DomainError("IM_CHANNEL_NOT_FOUND", "渠道不存在", 404)

    try:
        svc.soft_delete(channel)
        db.commit()
        return {"success": True}
    except Exception as exc:
        db.rollback()
        logger.exception("[IM] delete channel failed: %s", exc)
        raise DomainError("IM_DELETE_FAILED", "删除渠道失败", 500) from exc


# ── Toggle channel enabled ────────────────────────────────────────────────────

@router.post("/{channel_id}/toggle", response_model=dict)
async def toggle_channel(
    channel_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
) -> dict:
    """切换渠道启用状态。"""
    svc = IMChannelService(db)
    channel = svc.get_by_id_and_owner(channel_id, str(current_user.id))
    if channel is None:
        raise DomainError("IM_CHANNEL_NOT_FOUND", "渠道不存在", 404)

    try:
        toggled = svc.toggle(channel)
        db.commit()
        return {"data": _to_summary(toggled)}
    except Exception as exc:
        db.rollback()
        logger.exception("[IM] toggle channel failed: %s", exc)
        raise DomainError("IM_TOGGLE_FAILED", "切换状态失败", 500) from exc
