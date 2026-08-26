from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_review_project_analysis
from app.db.models import ProjectField
from app.db.repositories.project_field_repository import ProjectFieldRepository
from app.schemas.project_fields import ProjectFieldResponse, ProjectFieldReview
from app.services.audit_service import AuditService
from app.services.project_service import ProjectService


class ProjectFieldService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._fields = ProjectFieldRepository(session)
        self._projects = ProjectService(session)
        self._audit = AuditService(session)

    def list(self, project_id: UUID, actor_id: UUID, roles: set[str]) -> list[ProjectFieldResponse]:
        self._projects.get_visible(project_id, actor_id, roles)
        return [self._response(item) for item in self._fields.list_for_project(project_id)]

    def review(
        self, field_id: UUID, actor_id: UUID, roles: set[str], payload: ProjectFieldReview
    ) -> ProjectFieldResponse:
        field = self._fields.get(field_id, for_update=True)
        if field is None:
            raise DomainError("RESOURCE_NOT_FOUND", "Project field does not exist", 404)
        project = self._projects.get_visible(field.project_id, actor_id, roles)
        self._projects.require_writable(project)
        if not can_review_project_analysis(roles):
            raise DomainError("PERMISSION_DENIED", "No permission to review project fields", 403)
        if payload.review_status == "CONFIRMED" and field.primary_evidence_id is None:
            raise DomainError("EVIDENCE_REQUIRED", "Evidence is required before confirmation", 409)
        before = {"review_status": field.review_status, "review_note": field.review_note}
        now = datetime.now(UTC)
        field.review_status = payload.review_status
        field.review_note = payload.review_note
        field.reviewed_at = now
        field.reviewed_by = actor_id
        field.updated_at = now
        self._audit.record(
            actor_id=actor_id,
            action="REVIEW_PROJECT_FIELD",
            target_type="PROJECT_FIELD",
            target_id=field.id,
            project_id=field.project_id,
            before=before,
            after={"review_status": field.review_status, "review_note": field.review_note},
        )
        self._session.commit()
        return self._response(field)

    def review_in_project(
        self,
        project_id: UUID,
        field_id: UUID,
        actor_id: UUID,
        roles: set[str],
        payload: ProjectFieldReview,
    ) -> ProjectFieldResponse:
        field = self._fields.get(field_id)
        if field is None or field.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "Project field does not exist", 404)
        return self.review(field_id, actor_id, roles, payload)

    @staticmethod
    def _response(field: ProjectField) -> ProjectFieldResponse:
        return ProjectFieldResponse(
            id=field.id,
            project_id=field.project_id,
            field_code=field.field_code,
            value_json=field.value_json,
            confidence=field.confidence,
            review_status=field.review_status,
            primary_evidence_id=field.primary_evidence_id,
            reviewed_at=field.reviewed_at,
            review_note=field.review_note,
        )
