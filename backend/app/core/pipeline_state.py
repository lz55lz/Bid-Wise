"""Pipeline 状态机。

定义 DocumentVersion.parse_status 和 Task.status 的状态转换规则。

状态转换图：
    QUEUED → RUNNING → SUCCEEDED
                 ↓
               FAILED
"""

from enum import StrEnum

from app.core.errors import DomainError


class PipelineStatus(StrEnum):
    """Pipeline 任务状态枚举。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# 允许的状态转换映射
ALLOWED_TRANSITIONS: dict[PipelineStatus, set[PipelineStatus]] = {
    PipelineStatus.QUEUED: {PipelineStatus.RUNNING},
    PipelineStatus.RUNNING: {PipelineStatus.SUCCEEDED, PipelineStatus.FAILED},
    PipelineStatus.SUCCEEDED: set(),
    PipelineStatus.FAILED: {PipelineStatus.QUEUED},  # 允许重试
}


def can_transition(from_status: PipelineStatus, to_status: PipelineStatus) -> bool:
    """检查状态转换是否合法。"""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, set())


def transition(
    current: PipelineStatus | str, target: PipelineStatus | str
) -> PipelineStatus:
    """执行状态转换，非法时抛出 DomainError。

    Args:
        current: 当前状态
        target: 目标状态

    Returns:
        转换后的状态

    Raises:
        DomainError: 非法状态转换
    """
    current_status = PipelineStatus(current) if isinstance(current, str) else current
    target_status = PipelineStatus(target) if isinstance(target, str) else target

    if not can_transition(current_status, target_status):
        raise DomainError(
            code="INVALID_PIPELINE_TRANSITION",
            message=f"Cannot transition from {current_status.value} to {target_status.value}",
            status_code=400,
        )
    return target_status
