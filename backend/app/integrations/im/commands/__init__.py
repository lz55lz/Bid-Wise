"""IM 斜杠命令入口。"""

from app.integrations.im.commands.base import BaseCommand
from app.integrations.im.commands.cmd_clear import ClearCommand
from app.integrations.im.commands.cmd_help import HelpCommand
from app.integrations.im.commands.cmd_info import InfoCommand
from app.integrations.im.commands.cmd_quick_query import QuickQueryCommand
from app.integrations.im.commands.cmd_stop import StopCommand
from app.integrations.im.commands.context import CommandContext
from app.integrations.im.commands.registry import CommandRegistry


def create_default_registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(HelpCommand())
    registry.register(InfoCommand())
    registry.register(ClearCommand())
    registry.register(StopCommand())
    registry.register(QuickQueryCommand("projects", "查看可访问项目", "我有哪些项目"))
    registry.register(QuickQueryCommand("report", "查看当前项目分析摘要", "分析报告"))
    registry.register(QuickQueryCommand("risks", "查看当前项目风险", "风险报告"))
    registry.register(QuickQueryCommand("materials", "查看企业材料匹配缺口", "企业匹配"))
    return registry


__all__ = [
    "BaseCommand",
    "ClearCommand",
    "CommandContext",
    "CommandRegistry",
    "HelpCommand",
    "InfoCommand",
    "QuickQueryCommand",
    "StopCommand",
    "create_default_registry",
]
