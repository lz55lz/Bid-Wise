"""IM 集成核心 schemas 测试。"""

from app.integrations.im.schemas import IncomingMessage, Platform


class TestIncomingMessage:
    """统一入站消息模型测试。"""

    def test_should_create_text_message(self):
        """应能创建文本类型的 IncomingMessage。"""
        msg = IncomingMessage(
            platform=Platform.TELEGRAM,
            message_type="text",
            user_id="123456",
            chat_id="789012",
            content="我们能投这个项目吗？",
            message_id="msg-001",
        )

        assert msg.platform == Platform.TELEGRAM
        assert msg.message_type == "text"
        assert msg.user_id == "123456"
        assert msg.chat_id == "789012"
        assert msg.content == "我们能投这个项目吗？"
        assert msg.message_id == "msg-001"
        assert msg.chat_type == "direct"  # 默认值
        assert msg.thread_id is None

    def test_should_treat_group_chat_correctly(self):
        """群聊消息应能正确标识 chat_type 与 thread_id。"""
        msg = IncomingMessage(
            platform=Platform.TELEGRAM,
            message_type="text",
            user_id="123456",
            chat_id="-789012",
            chat_type="group",
            content="群里问一下",
            message_id="msg-002",
            thread_id="thread-1",
        )

        assert msg.chat_type == "group"
        assert msg.thread_id == "thread-1"
