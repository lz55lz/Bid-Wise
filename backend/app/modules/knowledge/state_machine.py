"""知识源解析生命周期的唯一迁移规则。"""

from typing import Protocol


class _HasParseStatus(Protocol):
    parse_status: str


class InvalidKnowledgeTransition(RuntimeError):
    """知识源解析发生非法状态跳转。"""


_ALLOWED: dict[str, frozenset[str]] = {
    "UPLOADED": frozenset({"QUEUED"}),
    "QUEUED": frozenset({"PARSING", "FAILED"}),
    "PARSING": frozenset({"INDEXING", "FAILED", "QUEUED"}),
    "INDEXING": frozenset({"READY", "FAILED", "QUEUED"}),
    "FAILED": frozenset({"QUEUED"}),
    "READY": frozenset(),
}


def transition(document: _HasParseStatus, target: str) -> None:
    current = document.parse_status
    if target not in _ALLOWED.get(current, frozenset()):
        raise InvalidKnowledgeTransition(f"非法知识源状态迁移：{current} -> {target}")
    document.parse_status = target
