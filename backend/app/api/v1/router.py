from fastapi import APIRouter

from app.api.v1 import (
    advanced,
    analysis,
    audits,
    auth,
    chat,
    decisions,
    documents,
    enterprises,
    evidences,
    im,
    im_channel,
    matches,
    materials,
    projects,
    reports,
    requirements,
    risks,
    rules,
)
from app.api.v1.chat import chat_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(advanced.router)
api_router.include_router(analysis.router)
api_router.include_router(projects.router)
api_router.include_router(documents.router)
api_router.include_router(enterprises.router)
api_router.include_router(evidences.router)
api_router.include_router(requirements.router)
api_router.include_router(decisions.router)
api_router.include_router(reports.router)
api_router.include_router(materials.router)
api_router.include_router(matches.router)
api_router.include_router(risks.router)
api_router.include_router(rules.router)
api_router.include_router(audits.router)
api_router.include_router(chat.router)  # /api/v1/sessions 前缀：会话管理
api_router.include_router(chat_router)  # /api/v1/chat 前缀：唯一流式会话入口
api_router.include_router(im.router)
api_router.include_router(im.callback_router)
api_router.include_router(im_channel.router)
