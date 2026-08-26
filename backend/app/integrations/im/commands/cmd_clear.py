"""/clear 命令。"""

from sqlalchemy import update

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class ClearCommand(BaseCommand):
    """清空当前对话记忆。"""

    @property
    def name(self) -> str:
        return "clear"

    @property
    def description(self) -> str:
        return "清空对话记忆"

    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        if not ctx.channel:
            return CommandResult(
                action="reply",
                content="当前渠道未绑定项目，无法清空对话。",
            )

        if not ctx.session_id or not ctx.db_session:
            return CommandResult(
                action="reply",
                content="当前没有活跃的对话会话。",
            )

        try:
            db = ctx.db_session

            # 软删除 ChatSession（不清物理记录，保留审计价值）
            from datetime import UTC, datetime

            from app.db.models.session import Session as ChatSession

            session = db.get(ChatSession, ctx.session_id)
            if session:
                session.deleted_at = datetime.now(UTC)
                db.flush()

            # 软删除 IMChannelSession 映射
            from app.db.models import IMChannelSession

            stmt = (
                update(IMChannelSession)
                .where(IMChannelSession.session_id == ctx.session_id)
                .values(deleted_at=datetime.now(UTC))
            )
            db.execute(stmt)
            db.commit()

            return CommandResult(
                action="clear",
                content="对话记忆已清空，下一条消息将开启新对话。",
            )

        except Exception as exc:
            return CommandResult(
                action="reply",
                content=f"清空失败：{exc}，请稍后再试。",
            )
