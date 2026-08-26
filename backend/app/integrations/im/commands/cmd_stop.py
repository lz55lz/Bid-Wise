"""/stop 命令，中止正在进行的处理。"""

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class StopCommand(BaseCommand):
    """中止正在进行的处理。"""

    @property
    def name(self) -> str:
        return "stop"

    @property
    def description(self) -> str:
        return "中止当前处理"

    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        # 当前 lei 无流式输出，/stop 仅作确认回复
        return CommandResult(
            action="stop",
            content="已收到停止请求，当前无进行中的处理。",
        )
