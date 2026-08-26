from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ProjectField


class ProjectFieldRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, field_id: UUID, *, for_update: bool = False) -> ProjectField | None:
        statement = select(ProjectField).where(ProjectField.id == field_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find(
        self, project_id: UUID, field_code: str, *, for_update: bool = False
    ) -> ProjectField | None:
        statement = select(ProjectField).where(
            ProjectField.project_id == project_id, ProjectField.field_code == field_code
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def find_by_codes(
        self, project_id: UUID, field_codes: Iterable[str], *, for_update: bool = False
    ) -> ProjectField | None:
        """Find the latest field in a caller-provided, equivalent code set."""
        codes = tuple(dict.fromkeys(field_codes))
        if not codes:
            return None
        statement = (
            select(ProjectField)
            .where(
                ProjectField.project_id == project_id,
                ProjectField.field_code.in_(codes),
            )
            .order_by(ProjectField.updated_at.desc(), ProjectField.id)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_for_project(self, project_id: UUID) -> list[ProjectField]:
        statement = (
            select(ProjectField)
            .where(ProjectField.project_id == project_id)
            .order_by(ProjectField.field_code, ProjectField.created_at, ProjectField.id)
        )
        return list(self._session.scalars(statement))

    def add(self, field: ProjectField) -> None:
        self._session.add(field)
