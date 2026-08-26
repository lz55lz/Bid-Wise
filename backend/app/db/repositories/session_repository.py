"""Session and Message repositories — port from WeKnora."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.session import Message
from app.db.models.session import Session as ChatSession


class SessionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_session(self, session_id: str) -> ChatSession | None:
        return self._session.get(ChatSession, session_id)

    def create_session(
        self,
        project_id: str | None,
        user_id: str,
        title: str = "新对话",
    ) -> ChatSession:
        session = ChatSession(
            id=str(uuid4()),
            project_id=project_id,
            user_id=user_id,
            title=title,
        )
        self._session.add(session)
        self._session.flush()
        return session

    def update_title(self, session_id: str, title: str) -> None:
        session = self.get_session(session_id)
        if session:
            session.title = title
            session.updated_at = datetime.now(UTC)
            self._session.flush()

    def list_sessions(
        self,
        project_id: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ChatSession]:
        q = select(ChatSession).where(ChatSession.deleted_at.is_(None))
        if project_id:
            q = q.where(ChatSession.project_id == project_id)
        if user_id:
            q = q.where(ChatSession.user_id == user_id)
        q = q.order_by(ChatSession.created_at.desc()).limit(limit).offset(offset)
        return list(self._session.execute(q).scalars().all())


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create_message(
        self,
        session_id: str,
        role: str,
        content: str,
        knowledge_references: list[dict] | None = None,
        is_fallback: bool = False,
    ) -> Message:
        message = Message(
            id=str(uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            knowledge_references=knowledge_references,
            is_completed=True,
            is_fallback=is_fallback,
        )
        self._session.add(message)
        self._session.flush()
        return message

    def get_last_assistant_message(self, session_id: str) -> Message | None:
        """最近一条有 knowledge_references 的 assistant 消息。"""
        q = (
            select(Message)
            .where(
                Message.session_id == session_id,
                Message.role == "assistant",
                Message.knowledge_references.isnot(None),
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
        return self._session.execute(q).scalars().first()

    def list_session_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Message]:
        q = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.execute(q).scalars().all())
