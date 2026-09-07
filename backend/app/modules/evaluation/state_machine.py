"""评测运行的显式业务状态转移。"""

from typing import Protocol


class _StatusRecord(Protocol):
    status: str


class InvalidEvaluationTransition(ValueError):
    """调用方试图跳过评测运行阶段。"""


_TRANSITIONS = {
    "QUEUED": frozenset({"RUNNING", "FAILED"}),
    "RUNNING": frozenset({"QUEUED", "SUCCEEDED", "FAILED"}),
    "FAILED": frozenset({"QUEUED"}),
    "SUCCEEDED": frozenset(),
}


def transition(run: _StatusRecord, target: str) -> None:
    if target not in _TRANSITIONS.get(run.status, frozenset()):
        raise InvalidEvaluationTransition(f"不允许从 {run.status} 转换到 {target}")
    run.status = target
