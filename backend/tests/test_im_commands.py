"""IM 斜杠命令系统测试。"""

import pytest

from app.integrations.im.commands.cmd_clear import ClearCommand
from app.integrations.im.commands.cmd_help import HelpCommand
from app.integrations.im.commands.registry import CommandRegistry
from app.integrations.im.schemas import (
    ChatType,
    IncomingMessage,
    MessageType,
    Platform,
)


@pytest.fixture
def incoming():
    return IncomingMessage(
        platform=Platform.WECOM,
        message_type=MessageType.TEXT,
        user_id="user-123",
        chat_id="chat-456",
        chat_type=ChatType.DIRECT,
        content="/help",
        message_id="msg-100",
    )


class TestCommandRegistry:
    """命令注册表测试。"""

    @pytest.fixture
    def registry(self):
        reg = CommandRegistry()
        reg.register(HelpCommand())
        reg.register(ClearCommand())
        return reg

    def test_should_parse_command(self, registry):
        """应能解析斜杠命令与参数。"""
        name, args = registry.parse("/search 资质要求")

        assert name == "search"
        assert args == "资质要求"

    def test_should_return_none_for_non_command(self, registry):
        """非命令文本应返回 None。"""
        result = registry.parse("我们能投这个标吗？")

        assert result is None

    def test_should_handle_command_without_args(self, registry):
        """无参数命令应返回空字符串参数。"""
        name, args = registry.parse("/help")

        assert name == "help"
        assert args == ""

    @pytest.mark.asyncio
    async def test_should_execute_help_command(self, registry, incoming):
        """应能执行 /help 命令。"""
        result = await registry.execute(incoming, "/help")

        assert result is not None
        assert result.action == "reply"
        assert "/help" in result.content
        assert "/clear" in result.content

    @pytest.mark.asyncio
    async def test_should_execute_clear_command_without_session(self, registry, incoming):
        """无活跃会话时 /clear 应回复提示。"""
        result = await registry.execute(
            incoming, "/clear",
            channel_data={"id": "ch-1", "project_id": "proj-1", "session_mode": "user"},
            session_id=None,
            db_session=None,
        )

        assert result is not None
        assert result.action == "reply"
        assert "没有活跃的对话会话" in result.content

    @pytest.mark.asyncio
    async def test_should_reply_unknown_command(self, registry, incoming):
        """未知命令应回复错误提示。"""
        result = await registry.execute(incoming, "/unknown")

        assert result is not None
        assert result.action == "reply"
        assert "未知指令" in result.content

    @pytest.mark.asyncio
    async def test_should_return_none_for_plain_text(self, registry, incoming):
        """普通文本应返回 None，交给 QA 处理。"""
        result = await registry.execute(incoming, "我们能投这个标吗？")

        assert result is None
