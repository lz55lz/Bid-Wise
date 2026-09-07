"""Evidence 索引任务生命周期的唯一迁移规则。"""

from typing import Protocol


class _HasStatus(Protocol):
    status: str


_ALLOWED: dict[str, frozenset[str]] = {
    "QUEUED": frozenset({"RUNNING", "FAILED", "SUCCEEDED"}),
    "RUNNING": frozenset({"QUEUED", "SUCCEEDED", "FAILED"}),
    "SUCCEEDED": frozenset(),
    "FAILED": frozenset(),
}


def transition(job: _HasStatus, target: str) -> None:
    if target not in _ALLOWED.get(job.status, frozenset()):
        raise RuntimeError(f"非法 Evidence 索引状态迁移：{job.status} -> {target}")
    job.status = target
