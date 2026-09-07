"""分析任务的显式业务状态转移。"""

from typing import Protocol


class _StatusRecord(Protocol):
    status: str


class InvalidAnalysisTransition(ValueError):
    """调用方试图跳过分析任务的业务阶段。"""


_FINDING_TRANSITIONS = {
    "QUEUED": frozenset({"RUNNING", "FAILED"}),
    "RUNNING": frozenset({"QUEUED", "SUCCEEDED", "FAILED"}),
    "FAILED": frozenset({"QUEUED"}),
    "SUCCEEDED": frozenset(),
}

_FULL_TRANSITIONS = {
    "QUEUED": frozenset({"RUNNING", "FAILED"}),
    "RUNNING": frozenset({"QUEUED", "REPORT_QUEUED", "FAILED"}),
    "REPORT_QUEUED": frozenset({"SUCCEEDED", "FAILED"}),
    "FAILED": frozenset({"QUEUED"}),
    "SUCCEEDED": frozenset(),
}


def transition_finding(job: _StatusRecord, target: str) -> None:
    _transition(job, target, _FINDING_TRANSITIONS)


def transition_full(run: _StatusRecord, target: str) -> None:
    _transition(run, target, _FULL_TRANSITIONS)


def _transition(record: _StatusRecord, target: str, transitions: dict[str, frozenset[str]]) -> None:
    if target not in transitions.get(record.status, frozenset()):
        raise InvalidAnalysisTransition(f"不允许从 {record.status} 转换到 {target}")
    record.status = target
