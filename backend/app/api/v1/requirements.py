# ruff: noqa: E501
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.requirements.service import RequirementService

router = APIRouter(prefix="/projects/{project_id}/requirements", tags=["招标需求"])


class RequirementReviewRequest(BaseModel):
    status: str = Field(pattern="^(CONFIRMED|REJECTED)$")
    note: str | None = Field(default=None, max_length=2000)


class RequirementBulkReviewRequest(RequirementReviewRequest):
    requirement_ids: list[UUID] = Field(min_length=1)


class ProjectFieldReviewRequest(BaseModel):
    status: str = Field(pattern="^(CONFIRMED|REJECTED)$")
    note: str | None = Field(default=None, max_length=2000)


def _response(item) -> dict[str, object]:
    """需求返回保留条件 DSL，方便前端展示待补充的具体资格条件。"""
    return {
        "id": str(item.id),
        "category": item.category,
        "title": item.title,
        "description": item.description,
        "conditions": item.conditions,
        "is_mandatory": item.is_mandatory,
        "score": item.score,
        "confidence": item.confidence,
        "review_status": item.review_status,
        "review_note": item.review_note,
        "primary_evidence_id": None
        if item.primary_evidence_id is None
        else str(item.primary_evidence_id),
        "evidence_ids": []
        if item.primary_evidence_id is None
        else [str(item.primary_evidence_id)],
        "extraction_source": item.extraction_source,
        "updated_at": item.updated_at,
    }


def _field_response(item) -> dict[str, object]:
    return {
        "id": str(item.id),
        "field_code": item.field_code,
        "value": item.value,
        "confidence": item.confidence,
        "review_status": item.review_status,
        "review_note": item.review_note,
        "primary_evidence_id": None
        if item.primary_evidence_id is None
        else str(item.primary_evidence_id),
        "extraction_source": item.extraction_source,
        "updated_at": item.updated_at,
    }


@router.get("/fields")
async def list_project_fields(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    items = await RequirementService(session).list_fields(project_id, current_user)
    return [_field_response(item) for item in items]


@router.patch("/fields/{field_id}/review")
async def review_project_field(
    project_id: UUID,
    field_id: UUID,
    payload: ProjectFieldReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, object]:
    item = await RequirementService(session).review_field(
        project_id, field_id, current_user, payload.status, payload.note
    )
    return _field_response(item)


@router.get("")
async def list_requirements(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    items = await RequirementService(session).list_for_project(project_id, current_user)
    return [_response(item) for item in items]


@router.patch("/{requirement_id}/review")
async def review_requirement(
    project_id: UUID,
    requirement_id: UUID,
    payload: RequirementReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, str]:
    item = await RequirementService(session).review(
        project_id, requirement_id, current_user, payload.status, payload.note
    )
    return _response(item)


@router.post("/bulk-review")
async def bulk_review_requirements(
    project_id: UUID,
    payload: RequirementBulkReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> list[dict[str, object]]:
    items = await RequirementService(session).bulk_review(
        project_id,
        payload.requirement_ids,
        current_user,
        payload.status,
        payload.note,
    )
    return [_response(item) for item in items]
