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
