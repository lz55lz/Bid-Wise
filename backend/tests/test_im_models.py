"""IM 数据模型测试。"""

from app.db.models.im_channel import IMChannel, IMChannelSession


class TestIMChannel:
    """IM 渠道模型测试。"""

    def test_should_create_channel_with_defaults(self):
        """应能创建带默认值的 IM 渠道实例。"""
        channel = IMChannel(
            name="测试 Telegram 机器人",
            platform="telegram",
            credentials={"bot_token": "secret-token"},
            owner_user_id="owner-1",
        )

        assert channel.name == "测试 Telegram 机器人"
        assert channel.platform == "telegram"
        assert channel.mode == "webhook"
        assert channel.output_mode == "full"
        assert channel.session_mode == "user"
        assert channel.enabled is True
        assert channel.credentials["bot_token"] == "secret-token"


class TestIMChannelSession:
    """IM 渠道会话映射模型测试。"""

    def test_should_create_session_mapping(self):
        """应能创建平台会话与 WeKnora 会话的映射。"""
        mapping = IMChannelSession(
            channel_id="channel-1",
            platform_user_id="user-123",
            chat_id="chat-456",
            thread_id="thread-789",
            session_id="session-abc",
        )

        assert mapping.channel_id == "channel-1"
        assert mapping.platform_user_id == "user-123"
        assert mapping.chat_id == "chat-456"
        assert mapping.thread_id == "thread-789"
        assert mapping.session_id == "session-abc"
