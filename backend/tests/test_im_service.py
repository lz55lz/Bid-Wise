"""IM Service 测试。"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import Request

from app.integrations.im.adapters.base import IMAdapter
from app.integrations.im.schemas import (
    ChatType,
    IncomingMessage,
    MessageType,
    Platform,
    ReplyMessage,
)
from app.integrations.im.service import IMService, _build_user_key
from app.services.rag_stream import sse_event


class FakeAdapter(IMAdapter):
    """测试用适配器，记录所有发送的回复。"""

    def __init__(self):
        super().__init__({})
        self.replies: list[str] = []

    @property
    def platform(self) -> str:
        return "fake"

    async def verify_callback(self, request: Request) -> bool:
        return True

    async def parse_callback(self, request: Request) -> IncomingMessage | None:
        return None

    async def send_reply(self, incoming: IncomingMessage, reply: ReplyMessage) -> None:
        self.replies.append(reply.content)


class TestIMServiceQAPipeline:
    """QA 流水线测试。"""

    @pytest.mark.asyncio
    async def test_should_invoke_shared_conversation_service_and_send_reply(self):
        """IM 应消费共享流式会话内核的最终回答。"""
        captured_turns = []

        async def fake_stream(_service, turn):
            captured_turns.append(turn)
            yield sse_event({"type": "status", "stage": "routing"})
            yield sse_event({"type": "done", "answer": "这是统一内核返回的答案"})

        service = IMService()
        adapter = FakeAdapter()
        incoming = IncomingMessage(
            platform=Platform.WECOM,
            message_type=MessageType.TEXT,
            user_id="user-123",
            chat_id="chat-456",
            chat_type=ChatType.GROUP,
            content="竞争对手有哪些",
            message_id="msg-qa-1",
        )
        channel_data = {
            "id": "channel-1",
            "owner_user_id": "00000000-0000-0000-0000-000000000001",
        }
        db = MagicMock()

        with patch("app.integrations.im.service.ConversationStreamService.stream", fake_stream):
            await service._handle_qa_pipeline(
                incoming,
                adapter,
                channel_data,
                db,
                channel_session_id="channel-session-1",
                chat_session_id="session-1",
            )

        assert adapter.replies == ["这是统一内核返回的答案"]
        turn = captured_turns[0]
        assert turn.session_id == "session-1"
        assert turn.question == "竞争对手有哪些"

    @pytest.mark.asyncio
    async def test_should_reply_error_when_session_missing(self):
        """没有 chat_session_id 时应提示用户。"""
        service = IMService()
        adapter = FakeAdapter()
        incoming = IncomingMessage(
            platform=Platform.WECOM,
            message_type=MessageType.TEXT,
            user_id="user-123",
            chat_id="chat-456",
            chat_type=ChatType.GROUP,
            content="hi",
            message_id="msg-qa-2",
        )

        await service._handle_qa_pipeline(
            incoming,
            adapter,
            {"id": "channel-1"},
            MagicMock(),
            None,
            None,
        )

        assert len(adapter.replies) == 1
        assert "未关联会话" in adapter.replies[0]

    def test_should_build_key_for_direct_chat(self):
        """私聊应构建正确的用户 key。"""
        msg = IncomingMessage(
            platform=Platform.WECOM,
            message_type=MessageType.TEXT,
            user_id="user-123",
            chat_id="",
            chat_type=ChatType.DIRECT,
            content="hi",
            message_id="msg-1",
        )

        key = _build_user_key("channel-1", msg)

        assert key == "wecom:channel-1:user-123:"

    def test_should_build_key_for_group_thread(self):
        """群聊带话题应构建正确的用户 key。"""
        msg = IncomingMessage(
            platform=Platform.WECOM,
            message_type=MessageType.TEXT,
            user_id="user-123",
            chat_id="chat-456",
            chat_type=ChatType.GROUP,
            content="hi",
            message_id="msg-2",
            thread_id="thread-789",
        )

        key = _build_user_key("channel-1", msg)

        assert key == "wecom:channel-1:user-123:chat-456:thread-789"


class TestIMServiceDedup:
    """消息去重测试。"""

    def test_should_detect_duplicate_message(self):
        """应能检测重复消息。"""
        service = IMService()
        service.mark_message_processed("msg-1")

        assert service.is_message_processed("msg-1") is True
        assert service.is_message_processed("msg-2") is False


class TestIMServiceRateLimit:
    """限流测试。"""

    def test_should_allow_within_limit(self):
        """在限制范围内应允许通过。"""
        service = IMService(max_messages=3, window_seconds=60)

        assert service.check_rate_limit("key-1") is True
        service.record_request("key-1")
        service.record_request("key-1")
        assert service.check_rate_limit("key-1") is True

    def test_should_block_over_limit(self):
        """超过限制应拒绝。"""
        service = IMService(max_messages=2, window_seconds=60)

        service.record_request("key-1")
        service.record_request("key-1")

        assert service.check_rate_limit("key-1") is False

    def test_should_track_different_keys_separately(self):
        """不同 key 应独立计数。"""
        service = IMService(max_messages=2, window_seconds=60)

        service.record_request("key-1")
        service.record_request("key-1")
        service.record_request("key-2")

        assert service.check_rate_limit("key-1") is False
        assert service.check_rate_limit("key-2") is True
