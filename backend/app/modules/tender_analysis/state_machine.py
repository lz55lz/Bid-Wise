"""投标管线运行的显式业务状态转移。"""

from typing import Protocol


class _StatusRecord(Protocol):
    status: str


class InvalidTenderPipelineTransition(ValueError):
    """调用方试图跳过投标管线的业务阶段。"""


_TRANSITIONS = {
    "QUEUED": frozenset({"RUNNING", "FAILED", "CANCELLED"}),
    "RUNNING": frozenset({"WAITING_HUMAN_REVIEW", "SUCCEEDED", "FAILED", "CANCELLED"}),
    "WAITING_HUMAN_REVIEW": frozenset({"RESUME_QUEUED", "CANCELLED"}),
    "RESUME_QUEUED": frozenset({"RUNNING", "FAILED", "CANCELLED"}),
    "FAILED": frozenset({"QUEUED", "RESUME_QUEUED"}),
    "SUCCEEDED": frozenset(),
    "CANCELLED": frozenset(),
}


def transition(run: _StatusRecord, target: str) -> None:
    if target not in _TRANSITIONS.get(run.status, frozenset()):
        raise InvalidTenderPipelineTransition(f"不允许从 {run.status} 转换到 {target}")
    run.status = target
