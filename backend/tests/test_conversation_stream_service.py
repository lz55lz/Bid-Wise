from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID

from app.services.conversation_stream_service import ConversationStreamService


def test_first_question_replaces_legacy_new_session_title() -> None:
    session = Mock()
    service = ConversationStreamService(session, Mock())
    service._messages = Mock()
    chat_session = SimpleNamespace(
        id=UUID("12345678-1234-5678-1234-567812345678"), title="新会话"
    )

    service._persist_turn(
        chat_session,
        "招标人确定中标人的依据是什么？",
        "回答",
        [],
        False,
    )

    assert chat_session.title == "招标人确定中标人的依据是什么？"
    session.commit.assert_called_once_with()


def test_recent_history_is_bounded_and_marked_untrusted() -> None:
    service = ConversationStreamService(Mock(), Mock())
    service._messages = Mock()
    service._messages.list_recent_session_messages.return_value = [
        SimpleNamespace(role="user", content="上一条问题", created_at=datetime.now(UTC)),
        SimpleNamespace(
            role="assistant",
            content="上一条回答 " * 500,
            created_at=datetime.now(UTC) + timedelta(seconds=1),
        ),
    ]

    result = service._with_recent_history(SimpleNamespace(id="session-1"), "它的截止日期呢？")

    assert "未可信对话历史" in result
    assert "当前用户问题：它的截止日期呢？" in result
    assert len(result) <= 2_000
    service._messages.list_recent_session_messages.assert_called_once_with("session-1", limit=6)
