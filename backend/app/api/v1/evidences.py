from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.schemas.evidences import EvidenceResponse
from app.services.evidence_service import EvidenceService

router = APIRouter(prefix="/evidences", tags=["evidences"])


@router.get("/{evidence_id}", response_model=EvidenceResponse)
def get_evidence(
    evidence_id: UUID,
    project_id: UUID | None = None,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EvidenceResponse:
    service = EvidenceService(session)
    if project_id is not None:
        return service.get_visible_for_project(
            evidence_id, project_id, current_user.id, current_user.role_codes
        )
    return service.get_visible(evidence_id, current_user.id, current_user.role_codes)
