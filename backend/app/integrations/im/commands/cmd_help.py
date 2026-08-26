"""/help 命令。"""

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class HelpCommand(BaseCommand):
    """显示可用命令列表。"""

    @property
    def name(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "查看可用命令"

    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        del args
        commands = ctx.registry.get_commands()
        lines = [
            "投标参谋快捷入口：",
            "/help - 查看可用命令",
            "/projects - 查看并选择项目",
            "/report - 查看项目分析摘要",
            "/risks - 查看项目风险",
            "/materials - 查看企业材料缺口",
            "\n也可以直接自然语言提问，例如“投标有哪些注意事项”。",
            "\n其他：",
        ]
        for name in ("clear", "info", "stop"):
            cmd = commands.get(name)
            if cmd is not None:
                lines.append(f"/{name} - {cmd.description}")
        return CommandResult(action="reply", content="\n".join(lines))
