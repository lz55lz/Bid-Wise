"""IM 集成 API 路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.db.models.im_channel import IMChannel
from app.db.session import get_db_session
from app.integrations.im.runtime import build_adapter, serialize_channel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/im", tags=["IM"])
callback_router = APIRouter(prefix="/wecom", tags=["IM callback"])


def _get_channel(db: Session, channel_id: str) -> IMChannel:
    """从数据库加载 IM 渠道。"""
    from sqlalchemy import select

    stmt = select(IMChannel).where(IMChannel.id == channel_id, IMChannel.deleted_at.is_(None))
    channel = db.execute(stmt).scalar_one_or_none()
    if channel is None:
        raise DomainError("IM_CHANNEL_NOT_FOUND", "IM 渠道不存在", 404)
    if not channel.enabled:
        raise DomainError("IM_CHANNEL_DISABLED", "IM 渠道已停用", 400)
    return channel


@router.get("/callback/{channel_id}")
@router.post("/callback/{channel_id}")
@router.get("/inbound/{channel_id}")
@router.post("/inbound/{channel_id}")
@callback_router.get("/inbound/{channel_id}")
@callback_router.post("/inbound/{channel_id}")
async def im_callback(
    channel_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
) -> PlainTextResponse:
    """企业微信等 IM 平台回调入口。"""
    try:
        channel = _get_channel(db, channel_id)
        request_id = getattr(request.state, "request_id", "-")

        channel_data = serialize_channel(channel)

        # URL 验证（企业微信首次配置回调时）
        if request.method == "GET":
            logger.info(
                "[IM] callback URL verification channel=%s platform=%s request_id=%s",
                channel_id, channel_data.get("platform"), request_id,
            )
            adapter = build_adapter(channel_data)
            echostr = await adapter.handle_url_verification(request)
            logger.info(
                "[IM] callback URL verification result channel=%s success=%s request_id=%s",
                channel_id, bool(echostr), request_id,
            )
            if echostr:
                return PlainTextResponse(content=echostr)
            logger.warning("[IM] URL verification FAILED for channel %s", channel_id)
            return PlainTextResponse(content="", status_code=400)

        # 校验回调签名
        adapter = build_adapter(channel_data)
        if not await adapter.verify_callback(request):
            logger.warning("[IM] invalid callback signature channel=%s request_id=%s", channel_id, request_id)
            return PlainTextResponse(content="", status_code=403)

        incoming = await adapter.parse_callback(request)
        if incoming is None:
            return PlainTextResponse(content="success")

        redis_pool = getattr(request.app.state, "redis_pool", None)
        if redis_pool is None:
            logger.error("[IM] ARQ Redis pool unavailable; callback will be retried")
            return PlainTextResponse(content="", status_code=503)
        await redis_pool.enqueue_job(
            "process_im_message",
            channel_id,
            incoming.model_dump(mode="json"),
            _job_id=f"im:{channel_id}:{incoming.message_id}",
        )
        logger.info("[IM] callback accepted channel=%s message_id=%s request_id=%s", channel_id, incoming.message_id, request_id)
        return PlainTextResponse(content="success")

    except DomainError as exc:
        logger.warning("[IM] domain error: %s", exc)
        return PlainTextResponse(content="", status_code=exc.status_code or 400)
    except Exception as exc:
        logger.exception("[IM] callback error: %s", exc)
        return PlainTextResponse(content="", status_code=500)
