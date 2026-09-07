"""项目报告生成生命周期的唯一迁移规则。"""

from typing import Protocol


class _HasStatus(Protocol):
    status: str


_ALLOWED: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"GENERATING", "FAILED"}),
    "GENERATING": frozenset({"READY", "FAILED", "QUEUED"}),
    "FAILED": frozenset(),
    "READY": frozenset(),
}


def transition(report: _HasStatus, target: str) -> None:
    if target not in _ALLOWED.get(report.status, frozenset()):
        raise RuntimeError(f"非法报告状态迁移：{report.status} -> {target}")
    report.status = target
