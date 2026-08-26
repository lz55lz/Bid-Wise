"""/info 命令，显示当前渠道信息。"""

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.schemas import CommandResult


class InfoCommand(BaseCommand):
    """查看当前渠道的信息与能力。"""

    @property
    def name(self) -> str:
        return "info"

    @property
    def description(self) -> str:
        return "查看当前渠道信息"

    async def execute(self, ctx: CommandContext, args: str) -> CommandResult:
        ch = ctx.channel or {}

        name = ch.get("name", "未命名渠道")
        platform = ch.get("platform", "未知")
        mode = ch.get("mode", "")
        output_mode = ch.get("output_mode", "full")
        session_mode = ch.get("session_mode", "user")
        kb_id = ch.get("knowledge_base_id")
        agent_id = ch.get("agent_id")

        platform_labels = {
            "wecom": "企业微信",
            "feishu": "飞书",
            "lark": "Lark",
            "telegram": "Telegram",
            "dingtalk": "钉钉",
        }
        platform_name = platform_labels.get(platform, platform)

        lines = [
            f"🤖 **{name}**",
            "",
            f"**平台**：{platform_name}",
            f"**接入模式**：{mode}",
        ]

        if agent_id:
            lines.append(f"**绑定项目**：{agent_id}")

        if kb_id:
            lines.append(f"**知识库 ID**：{kb_id}")

        lines.extend([
            f"**会话模式**：{'按用户' if session_mode == 'user' else '按话题'}",
            f"**输出模式**：{'完整输出' if output_mode == 'full' else '流式输出'}",
            "",
            "---",
            "发送 `/help` 查看所有可用指令",
        ])

        return CommandResult(action="reply", content="\n".join(lines))
