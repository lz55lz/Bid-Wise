"""Mobile-friendly shortcuts that enter the shared conversation pipeline."""

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class QuickQueryCommand(BaseCommand):
    """Translate a short IM command into a channel-neutral query."""

    def __init__(self, name: str, description: str, query: str) -> None:
        self._name = name
        self._description = description
        self._query = query

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        del ctx, args
        return CommandResult(action="query", content=self._query)
