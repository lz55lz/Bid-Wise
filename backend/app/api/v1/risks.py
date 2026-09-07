# ruff: noqa: E501
from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.projects.service import ProjectService
from app.modules.risks.service import RiskService

router = APIRouter(prefix="/projects/{project_id}/risks", tags=["项目风险"])


class RiskReviewRequest(BaseModel):
    status: str = Field(pattern="^(ACCEPTED|RESOLVED|DISMISSED)$")
    resolution: str | None = Field(default=None, max_length=2000)


@router.post("/run")
async def run_risks(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, int]:
    await ProjectService(session).require_document_write(project_id, current_user)
    risks = await RiskService(session).run(project_id, current_user.id)
    return {"count": len(risks)}


@router.get("")
async def list_risks(
    project_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> list[dict[str, object]]:
    await ProjectService(session).require_project_access(project_id, current_user)
    risks = await RiskService(session).list(project_id)
    return [
        {
            "id": str(item.id),
            "project_id": str(item.project_id),
            "rule_code": item.rule_code,
            "rule_version_id": None if item.rule_version_id is None else str(item.rule_version_id),
            "risk_type": item.risk_type,
            "severity": item.severity,
            "title": item.title,
            "description": item.description,
            "status": item.status,
            "resolution": item.resolution,
            "trigger_data": item.trigger_data,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }
        for item in risks
    ]


@router.patch("/{risk_id}")
async def review_risk(
    project_id: UUID,
    risk_id: UUID,
    payload: RiskReviewRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, str]:
    await ProjectService(session).require_document_write(project_id, current_user)
    item = await RiskService(session).review(
        project_id, risk_id, payload.status, payload.resolution
    )
    return {"id": str(item.id), "status": item.status}
