"""Pipeline 状态管理器。

封装 DocumentVersion.parse_status 和 Task.status 的状态转换逻辑，
确保状态转换符合状态机规则。
"""

from uuid import UUID

from sqlalchemy.orm import Session

from app.core.pipeline_state import PipelineStatus, transition
from app.db.models import DocumentVersion, Task


class PipelineStateManager:
    """Pipeline 状态管理器。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def _update_version_status(self, version_id: UUID, status: PipelineStatus) -> None:
        """更新 DocumentVersion.parse_status。"""
        version = self._session.query(DocumentVersion).filter(
            DocumentVersion.id == version_id
        ).first()
        if version:
            version.parse_status = status.value
            self._session.commit()

    def _update_task_status(self, version_id: UUID, status: PipelineStatus) -> None:
        """更新对应 PIPELINE_DOCUMENT Task 的状态。"""
        task = self._session.query(Task).filter(
            Task.target_id == version_id,
            Task.task_type == "PIPELINE_DOCUMENT",
        ).order_by(Task.created_at.desc()).first()
        if task:
            task.status = status.value
            self._session.commit()

    def transition_version(self, version_id: UUID, new_status: PipelineStatus) -> None:
        """切换版本状态，校验转换合法性。"""
        version = self._session.query(DocumentVersion).filter(
            DocumentVersion.id == version_id
        ).first()
        if version:
            current = PipelineStatus(version.parse_status)
            transition(current, new_status)  # 非法转换会抛异常
            version.parse_status = new_status.value
            self._session.commit()

    def transition_task(self, version_id: UUID, new_status: PipelineStatus) -> None:
        """切换任务状态，校验转换合法性。"""
        task = self._session.query(Task).filter(
            Task.target_id == version_id,
            Task.task_type == "PIPELINE_DOCUMENT",
        ).order_by(Task.created_at.desc()).first()
        if task:
            current = PipelineStatus(task.status)
            transition(current, new_status)
            task.status = new_status.value
            self._session.commit()

    def on_pipeline_start(self, version_id: UUID) -> None:
        """任务开始执行：QUEUED → RUNNING"""
        self.transition_version(version_id, PipelineStatus.RUNNING)
        self.transition_task(version_id, PipelineStatus.RUNNING)

    def on_pipeline_complete(self, version_id: UUID) -> None:
        """执行完成：RUNNING → SUCCEEDED"""
        self.transition_version(version_id, PipelineStatus.SUCCEEDED)
        self.transition_task(version_id, PipelineStatus.SUCCEEDED)

    def on_pipeline_fail(self, version_id: UUID) -> None:
        """执行失败：RUNNING → FAILED"""
        self.transition_version(version_id, PipelineStatus.FAILED)
        self.transition_task(version_id, PipelineStatus.FAILED)
