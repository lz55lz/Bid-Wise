"""项目文档解析生命周期的唯一迁移规则。"""

from typing import Protocol


class _HasParseStatus(Protocol):
    parse_status: str


class InvalidDocumentTransition(RuntimeError):
    """代码试图绕开文档 Pipeline 的合法迁移时抛出。"""


_ALLOWED: dict[str, frozenset[str]] = {
    "UPLOADED": frozenset({"QUEUED"}),
    "QUEUED": frozenset({"PARSING", "FAILED"}),
    "PARSING": frozenset({"CLEANING", "FAILED", "QUEUED"}),
    "CLEANING": frozenset({"BUILDING_EVIDENCE", "FAILED", "QUEUED"}),
    "BUILDING_EVIDENCE": frozenset({"INDEXING", "FAILED", "QUEUED"}),
    "INDEXING": frozenset({"READY", "FAILED", "QUEUED"}),
    "FAILED": frozenset({"QUEUED"}),
    "READY": frozenset(),
}


def transition(version: _HasParseStatus, target: str) -> None:
    current = version.parse_status
    if target not in _ALLOWED.get(current, frozenset()):
        raise InvalidDocumentTransition(f"非法文档状态迁移：{current} -> {target}")
    version.parse_status = target
