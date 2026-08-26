"""IM 斜杠命令基类。"""

from abc import ABC, abstractmethod

from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class BaseCommand(ABC):
    """斜杠命令基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """命令名（不含斜杠）。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """命令简介。"""
        ...

    @abstractmethod
    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        """执行命令。"""
        ...
