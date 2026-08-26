"""IM 斜杠命令注册表。"""

import re

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult, IncomingMessage


class CommandRegistry:
    """斜杠命令注册表。"""

    def __init__(self) -> None:
        self._commands: dict[str, BaseCommand] = {}

    def register(self, command: BaseCommand) -> None:
        """注册一个命令。"""
        self._commands[command.name] = command

    def get_commands(self) -> dict[str, BaseCommand]:
        """获取所有已注册命令。"""
        return dict(self._commands)

    @staticmethod
    def parse(text: str) -> tuple[str, str] | None:
        """解析斜杠命令，返回 (命令名, 参数)。"""
        text = text.strip()
        if not text.startswith("/"):
            return None
        # 匹配 /command args
        match = re.match(r"^/([a-zA-Z0-9_]+)(?:\s+(.*))?$", text)
        if not match:
            return None
        name = match.group(1).lower()
        args = (match.group(2) or "").strip()
        return name, args

    async def execute(
        self,
        incoming: IncomingMessage,
        text: str,
        channel_data: dict | None = None,
        session_id: str | None = None,
        db_session=None,
    ) -> CommandResult | None:
        """执行命令；如果不是命令则返回 None。"""
        parsed = self.parse(text)
        if not parsed:
            return None

        name, args = parsed
        ctx = CommandContext(
            incoming=incoming,
            registry=self,
            channel=channel_data,
            session_id=session_id,
            db_session=db_session,
        )
        command = self._commands.get(name)
        if command is None:
            return CommandResult(
                action="reply",
                content=f"未知指令 /{name}。输入 /help 查看可用命令。",
            )

        return await command.execute(ctx, args)
