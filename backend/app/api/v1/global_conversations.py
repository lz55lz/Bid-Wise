"""不绑定项目的通用法律咨询会话接口。"""

import json
from uuid import UUID

from fastapi import APIRouter, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.core.errors import DomainError
from app.modules.conversations.schemas import ConversationCreateRequest, ConversationMessageRequest, ConversationMessageResponse, ConversationResponse
from app.modules.conversations.service import ConversationService

router = APIRouter(prefix="/conversations", tags=["通用法律对话"])


@router.get("", response_model=list[ConversationResponse])
async def list_global_conversations(current_user: CurrentUser, session: DatabaseSession, settings: ApplicationSettings):
    return await ConversationService(session, settings).list_global_owned(current_user)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_global_conversation(payload: ConversationCreateRequest, current_user: CurrentUser, session: DatabaseSession, settings: ApplicationSettings):
    return await ConversationService(session, settings).create_global(current_user, payload)


@router.get("/{conversation_id}/messages", response_model=list[ConversationMessageResponse])
async def list_global_messages(conversation_id: UUID, current_user: CurrentUser, session: DatabaseSession, settings: ApplicationSettings):
    return await ConversationService(session, settings).list_messages(None, conversation_id, current_user)


@router.post("/{conversation_id}/messages/stream")
async def stream_global_conversation(conversation_id: UUID, payload: ConversationMessageRequest, current_user: CurrentUser, session: DatabaseSession, settings: ApplicationSettings):
    service = ConversationService(session, settings)
    async def events():
        try:
            async for event in service.ask_stream(None, conversation_id, current_user, payload.content):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except DomainError as exc:
            yield f"data: {json.dumps({'type': 'error', 'code': exc.code, 'message': exc.message}, ensure_ascii=False)}\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_global_conversation(conversation_id: UUID, current_user: CurrentUser, session: DatabaseSession, settings: ApplicationSettings):
    await ConversationService(session, settings).delete(None, conversation_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
