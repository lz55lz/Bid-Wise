from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_review_project_analysis
from app.db.models import Requirement
from app.db.repositories.requirement_repository import RequirementRepository
from app.schemas.requirements import RequirementBulkReview, RequirementResponse, RequirementReview
from app.services.audit_service import AuditService
from app.services.project_service import ProjectService


class RequirementService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._requirements = RequirementRepository(session)
        self._projects = ProjectService(session)
        self._audit = AuditService(session)

    def list(self, project_id: UUID, actor_id: UUID, roles: set[str]) -> list[RequirementResponse]:
        self._projects.get_visible(project_id, actor_id, roles)
        return [self._response(item) for item in self._requirements.list_for_project(project_id)]

    def review(
        self, requirement_id: UUID, actor_id: UUID, roles: set[str], payload: RequirementReview
    ) -> RequirementResponse:
        requirement = self._requirements.get(requirement_id)
        if requirement is None or requirement.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        project = self._projects.get_visible(requirement.project_id, actor_id, roles)
        self._projects.require_writable(project)
        if not can_review_project_analysis(roles):
            raise DomainError("PERMISSION_DENIED", "无权复核 Requirement", 403)
        evidence_ids = self._requirements.list_evidence_ids(requirement.id)
        if payload.review_status == "CONFIRMED" and not evidence_ids:
            raise DomainError("EVIDENCE_REQUIRED", "确认 Requirement 前必须关联 Evidence", 409)
        before = {
            "review_status": requirement.review_status,
            "review_note": requirement.review_note,
        }
        requirement.review_status = payload.review_status
        requirement.review_note = payload.review_note
        requirement.reviewed_at = datetime.now(UTC)
        requirement.reviewed_by = actor_id
        requirement.updated_at = requirement.reviewed_at
        self._audit.record(
            actor_id=actor_id,
            action="REVIEW_REQUIREMENT",
            target_type="REQUIREMENT",
            target_id=requirement.id,
            project_id=requirement.project_id,
            before=before,
        )
        self._session.commit()
        return self._response(requirement, evidence_ids)

    def review_in_project(
        self,
        project_id: UUID,
        requirement_id: UUID,
        actor_id: UUID,
        roles: set[str],
        payload: RequirementReview,
    ) -> RequirementResponse:
        """Apply the documented project-scoped route without trusting only an ID."""
        requirement = self._requirements.get(requirement_id)
        if (
            requirement is None
            or requirement.deleted_at is not None
            or requirement.project_id != project_id
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return self.review(requirement_id, actor_id, roles, payload)

    def bulk_review_in_project(
        self, project_id: UUID, actor_id: UUID, roles: set[str], payload: RequirementBulkReview
    ) -> list[RequirementResponse]:
        """Atomically review up to one active human-review queue."""
        project = self._projects.get_visible(project_id, actor_id, roles)
        self._projects.require_writable(project)
        if not can_review_project_analysis(roles):
            raise DomainError("PERMISSION_DENIED", "无权复核 Requirement", 403)
        requirement_ids = list(dict.fromkeys(payload.requirement_ids))
        requirements = [
            self._requirements.get(requirement_id) for requirement_id in requirement_ids
        ]
        invalid = any(
            item is None
            or item.deleted_at is not None
            or item.project_id != project_id
            or item.review_status != "PENDING"
            for item in requirements
        )
        if invalid:
            raise DomainError(
                "REQUIREMENT_REVIEW_NOT_ALLOWED", "仅可批量处理当前优先复核队列。", 409
            )
        evidence = self._requirements.list_evidence_ids_for_requirements(requirement_ids)
        if payload.review_status == "CONFIRMED" and any(
            not evidence[item_id] for item_id in requirement_ids
        ):
            raise DomainError("EVIDENCE_REQUIRED", "确认 Requirement 前必须关联 Evidence", 409)
        now = datetime.now(UTC)
        for item in requirements:
            assert item is not None
            self._audit.record(
                actor_id=actor_id,
                action="BULK_REVIEW_REQUIREMENT",
                target_type="REQUIREMENT",
                target_id=item.id,
                project_id=project_id,
                before={"review_status": item.review_status, "review_note": item.review_note},
            )
            item.review_status, item.review_note = payload.review_status, payload.review_note
            item.reviewed_at, item.reviewed_by, item.updated_at = now, actor_id, now
        self._session.commit()
        return [
            self._response(item, evidence[item.id])
            for item in requirements
            if item is not None
        ]

    def _response(
        self, requirement: Requirement, evidence_ids: list[UUID] | None = None
    ) -> RequirementResponse:
        return RequirementResponse(
            id=requirement.id,
            project_id=requirement.project_id,
            category=requirement.category,
            title=requirement.title,
            description=requirement.description,
            conditions=requirement.conditions,
            is_mandatory=requirement.is_mandatory,
            score=requirement.score,
            confidence=requirement.confidence,
            review_status=requirement.review_status,
            primary_evidence_id=requirement.primary_evidence_id,
            evidence_ids=evidence_ids
            if evidence_ids is not None
            else self._requirements.list_evidence_ids(requirement.id),
            reviewed_at=requirement.reviewed_at,
            review_note=requirement.review_note,
        )
