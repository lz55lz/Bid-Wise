"""Multi-turn conversation and session management API.

Provides:
- Chat session CRUD (/sessions prefix)
- Streaming QA endpoint (/chat/stream prefix, SSE)
"""

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.db.repositories.session_repository import MessageRepository, SessionRepository
from app.db.session import get_db_session
from app.services.conversation_stream_service import (
    ConversationStreamService,
    ConversationStreamTurn,
)

# Multi-turn QA routes (/chat prefix)
chat_router = APIRouter(prefix="/chat", tags=["chat-qa"])

logger = logging.getLogger(__name__)

# Session management routes (/sessions prefix)
router = APIRouter(prefix="/sessions", tags=["chat"])


class SessionCreate(BaseModel):
    """Request to create a new chat session."""
    title: str = Field(default="新对话")
    project_id: str | None = Field(default=None)


class SessionUpdate(BaseModel):
    """Request to update session title."""
    title: str


class CreateMessageRequest(BaseModel):
    """Request to append a message to a session."""
    role: str  # "user" or "assistant"
    content: str
    knowledge_references: list[dict] | None = None
    is_fallback: bool = False


class MessageResponse(BaseModel):
    """Single message in a session."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    knowledge_references: dict | None = None
    is_completed: bool = True
    is_fallback: bool = False
    created_at: str | None = None

    @field_validator("created_at", mode="before")
    @classmethod
    def dt_to_str(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class SessionListResponse(BaseModel):
    """Session summary for list views."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    project_id: str | None = None
    user_id: str
    is_pinned: bool = False
    pinned_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def dt_to_str(cls, v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.isoformat()
        return str(v)


class PaginatedSessionsResponse(BaseModel):
    """Paginated session list response."""
    items: list[SessionListResponse]
    total: int
    page: int
    page_size: int


class PaginatedMessagesResponse(BaseModel):
    """Paginated message list response."""
    items: list[MessageResponse]
    total: int


def get_session_repo(session: Session) -> SessionRepository:
    return SessionRepository(session)


def get_message_repo(session: Session) -> MessageRepository:
    return MessageRepository(session)


@router.get("", response_model=PaginatedSessionsResponse)
def list_sessions(
    # List chat sessions for current user (paginated).
    page: int = 1,
    page_size: int = 20,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PaginatedSessionsResponse:
    repo = get_session_repo(session)
    offset = (page - 1) * page_size
    sessions = repo.list_sessions(user_id=str(current_user.id), limit=page_size, offset=offset)
    all_sessions = repo.list_sessions(user_id=str(current_user.id), limit=10000, offset=0)
    return PaginatedSessionsResponse(
        items=[SessionListResponse.model_validate(s) for s in sessions],
        total=len(all_sessions),
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=SessionListResponse, status_code=status.HTTP_201_CREATED)
def create_session(
    # Create a new chat session.
    payload: SessionCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> SessionListResponse:
    repo = get_session_repo(session)
    s = repo.create_session(
        project_id=payload.project_id,
        user_id=str(current_user.id),
        title=payload.title,
    )
    return SessionListResponse.model_validate(s)


@router.get("/{session_id}", response_model=SessionListResponse)
def get_session(
    # Get a single session by ID.
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> SessionListResponse:
    from app.core.errors import DomainError

    repo = get_session_repo(session)
    s = repo.get_session(str(session_id))
    if not s or s.user_id != str(current_user.id):
        raise DomainError("NOT_FOUND", "Session not found", 404)
    return SessionListResponse.model_validate(s)


@router.put("/{session_id}", response_model=SessionListResponse)
def update_session(
    # Update session title.
    session_id: UUID,
    payload: SessionUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> SessionListResponse:
    from app.core.errors import DomainError

    repo = get_session_repo(session)
    s = repo.get_session(str(session_id))
    if not s or s.user_id != str(current_user.id):
        raise DomainError("NOT_FOUND", "Session not found", 404)
    repo.update_title(str(session_id), payload.title)
    session.refresh(s)
    return SessionListResponse.model_validate(s)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_session(
    # Soft delete a session (sets deleted_at).
    session_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    from app.core.errors import DomainError

    repo = get_session_repo(session)
    s = repo.get_session(str(session_id))
    if not s or s.user_id != str(current_user.id):
        raise DomainError("NOT_FOUND", "Session not found", 404)
    s.deleted_at = datetime.now(UTC)
    session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{session_id}/messages", response_model=PaginatedMessagesResponse)
def list_messages(
    # List messages in a session (newest first, limited).
    session_id: UUID,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> PaginatedMessagesResponse:
    from app.core.errors import DomainError

    session_repo = get_session_repo(session)
    s = session_repo.get_session(str(session_id))
    if not s or s.user_id != str(current_user.id):
        raise DomainError("NOT_FOUND", "Session not found", 404)

    msg_repo = get_message_repo(session)
    messages = msg_repo.list_session_messages(str(session_id), limit=limit)
    return PaginatedMessagesResponse(
        items=[MessageResponse.model_validate(m) for m in messages],
        total=len(messages),
    )


@router.post(
    "/{session_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_message(
    # Append a message to a session (user or assistant).
    session_id: UUID,
    payload: CreateMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> MessageResponse:
    from app.core.errors import DomainError

    session_repo = get_session_repo(session)
    s = session_repo.get_session(str(session_id))
    if not s or s.user_id != str(current_user.id):
        raise DomainError("NOT_FOUND", "Session not found", 404)

    msg_repo = get_message_repo(session)
    m = msg_repo.create_message(
        session_id=str(session_id),
        role=payload.role,
        content=payload.content,
        knowledge_references=payload.knowledge_references,
        is_fallback=payload.is_fallback,
    )
    return MessageResponse.model_validate(m)


class ChatStreamRequest(BaseModel):
    """Request contract for the streaming chat endpoint."""

    question: str = Field(..., min_length=1, max_length=2000)
    session_id: str | None = Field(default=None)
    project_id: UUID | None = Field(default=None)

    @field_validator("question")
    @classmethod
    def question_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


@chat_router.post("/stream")
async def stream_chat(
    # Shared SSE QA endpoint for PC and mobile.
    # Streams RAG-augmented answers as Server-Sent Events.
    payload: ChatStreamRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings=Depends(get_settings),
) -> StreamingResponse:
    service = ConversationStreamService(session, settings)

    async def generate():
        try:
            async for event in service.stream(
                ConversationStreamTurn(
                    question=payload.question,
                    session_id=payload.session_id,
                    project_id=payload.project_id,
                    actor_id=current_user.id,
                    role_codes=current_user.role_codes,
                )
            ):
                yield event
        except Exception:
            logger.exception("[chat/stream] conversation failed")
            from app.services.rag_stream import sse_event

            yield sse_event(
                {
                    "type": "error",
                    "code": "CONVERSATION_FAILED",
                    "message": "QA service temporarily unavailable, please try again later.",
                }
            )

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
