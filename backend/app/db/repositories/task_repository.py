from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Task, TaskEvent


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, task_id: UUID) -> Task | None:
        return self._session.get(Task, task_id)

    def get_for_update(self, task_id: UUID) -> Task | None:
        return self._session.scalar(select(Task).where(Task.id == task_id).with_for_update())

    def next_attempt(self, task_type: str, idempotency_key: str) -> int:
        value = self._session.scalar(
            select(func.max(Task.attempt)).where(
                Task.task_type == task_type, Task.idempotency_key == idempotency_key
            )
        )
        return int(value or 0) + 1

    def latest_for_target(self, task_type: str, target_id: UUID) -> Task | None:
        return self._session.scalar(
            select(Task)
            .where(Task.task_type == task_type, Task.target_id == target_id)
            .order_by(Task.attempt.desc())
            .limit(1)
        )

    def latest_failed_document_task(self, target_id: UUID) -> Task | None:
        """Return the failed pipeline stage that must be retried for a document version."""
        return self._session.scalar(
            select(Task)
            .where(
                Task.target_id == target_id,
                Task.target_type == "DOCUMENT_VERSION",
                Task.status == "FAILED",
                Task.task_type.in_(
                    (
                        "PIPELINE_DOCUMENT",
                        "PARSE_DOCUMENT",
                        "CLEAN_DOCUMENT",
                        "INDEX_DOCUMENT",
                        "EXTRACT_REQUIREMENTS",
                    )
                ),
            )
            .order_by(Task.completed_at.desc(), Task.created_at.desc(), Task.id.desc())
            .limit(1)
        )

    def latest_for_idempotency_key(self, task_type: str, idempotency_key: str) -> Task | None:
        return self._session.scalar(
            select(Task)
            .where(Task.task_type == task_type, Task.idempotency_key == idempotency_key)
            .order_by(Task.attempt.desc())
            .limit(1)
        )

    def add(self, task: Task) -> None:
        self._session.add(task)

    def add_event(self, event: TaskEvent) -> None:
        self._session.add(event)
