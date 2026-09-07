"""项目私有对话 HTTP 接口。"""

import json
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.modules.conversations.schemas import (
    ConversationCreateRequest,
    ConversationMessagePage,
    ConversationMessageRequest,
    ConversationMessageResponse,
    ConversationResponse,
    ConversationUpdateRequest,
)
from app.modules.conversations.service import ConversationService

router = APIRouter(prefix="/projects/{project_id}/conversations", tags=["项目对话"])


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    project_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> list[ConversationResponse]:
    """仅列出当前登录用户自己的项目会话。"""
    return await ConversationService(session, settings).list_owned(project_id, current_user)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    project_id: UUID,
    payload: ConversationCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ConversationResponse:
    """创建一个当前项目的私有问答会话。"""
    return await ConversationService(session, settings).create(project_id, current_user, payload)


@router.get("/{conversation_id}/messages", response_model=list[ConversationMessageResponse])
async def list_conversation_messages(
    project_id: UUID,
    conversation_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> list[ConversationMessageResponse]:
    """获取当前用户自己的会话消息。"""
    return await ConversationService(session, settings).list_messages(
        project_id, conversation_id, current_user
    )


@router.get("/{conversation_id}/message-page", response_model=ConversationMessagePage)
async def list_conversation_message_page(
    project_id: UUID,
    conversation_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ConversationMessagePage:
    return await ConversationService(session, settings).list_message_page(
        project_id, conversation_id, current_user, offset, limit
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    project_id: UUID,
    conversation_id: UUID,
    payload: ConversationUpdateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ConversationResponse:
    return await ConversationService(session, settings).update(
        project_id, conversation_id, current_user, payload
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    project_id: UUID,
    conversation_id: UUID,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> Response:
    await ConversationService(session, settings).delete(project_id, conversation_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{conversation_id}/messages", response_model=ConversationMessageResponse)
async def ask_conversation(
    project_id: UUID,
    conversation_id: UUID,
    payload: ConversationMessageRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> ConversationMessageResponse:
    """写入问题并运行服务端受控的只读 RAG 问答工作流。"""
    return await ConversationService(session, settings).ask(
        project_id, conversation_id, current_user, payload.content
    )


@router.post("/{conversation_id}/messages/stream")
async def stream_conversation(
    project_id: UUID,
    conversation_id: UUID,
    payload: ConversationMessageRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> StreamingResponse:
    """以 SSE 推送回答 token；完成事件中返回已持久化的消息和引用快照。"""
    service = ConversationService(session, settings)

    async def events():
        async for event in service.ask_stream(
            project_id, conversation_id, current_user, payload.content
        ):
            yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
