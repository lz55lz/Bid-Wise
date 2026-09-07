"""对话域仓储：仅执行会话及消息的数据访问。"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.conversations.models import Conversation, ConversationMessage


class ConversationRepository:
    """不在仓储层判断成员角色或调用模型。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_owned(
        self, conversation_id: UUID, project_id: UUID, user_id: UUID
    ) -> Conversation | None:
        """只查询当前项目、当前用户自己的会话。"""
        return await self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.project_id == project_id,
                Conversation.user_id == user_id,
            )
        )

    async def list_owned(self, project_id: UUID, user_id: UUID) -> list[Conversation]:
        """按最近活动排序，避免在 API 层拼接查询。"""
        statement = (
            select(Conversation)
            .where(Conversation.project_id == project_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_global_owned(self, conversation_id: UUID, user_id: UUID) -> Conversation | None:
        return await self._session.scalar(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.project_id.is_(None),
                Conversation.user_id == user_id,
            )
        )

    async def list_global_owned(self, user_id: UUID) -> list[Conversation]:
        statement = (
            select(Conversation)
            .where(Conversation.project_id.is_(None), Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_messages(self, conversation_id: UUID, limit: int) -> list[ConversationMessage]:
        """读取近期消息后再按时间正序返回，供 Agent 构造有限历史。"""
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(limit)
        )
        return list(reversed((await self._session.scalars(statement)).all()))

    async def list_message_page(
        self, conversation_id: UUID, offset: int, limit: int
    ) -> tuple[list[ConversationMessage], int]:
        total = int(
            await self._session.scalar(
                select(func.count())
                .select_from(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
            )
            or 0
        )
        statement = (
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
            .offset(offset)
            .limit(limit)
        )
        return list((await self._session.scalars(statement)).all()), total

    def add_conversation(self, conversation: Conversation) -> None:
        self._session.add(conversation)

    def add_message(self, message: ConversationMessage) -> None:
        self._session.add(message)

    async def delete_conversation(self, conversation: Conversation) -> None:
        await self._session.delete(conversation)
