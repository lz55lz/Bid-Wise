"""Bid rule template management API.

CRUD for reusable bid rule templates (e.g., qualification thresholds, scoring rules).
Rules are versioned; updating a rule creates a new version rather than modifying the existing one.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.db.session import get_db_session
from app.schemas.rules import RuleCreateRequest, RuleResponse, RuleVersionRequest
from app.services.rule_service import RuleService

router = APIRouter(prefix="/rules", tags=["rules"])


@router.get("", response_model=list[RuleResponse])
def list_rules(
    # List all rule templates the current user can access.
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[RuleResponse]:
    return RuleService(session).list(current_user.role_codes)


@router.post("", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(
    # Create a new rule template.
    payload: RuleCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> RuleResponse:
    return RuleService(session).create(current_user.id, current_user.role_codes, payload)


@router.patch("/{rule_id}", response_model=RuleResponse)
def version_rule(
    # Create a new version of an existing rule.
    # Does not modify the current version; creates a new version entry.
    rule_id: UUID,
    payload: RuleVersionRequest,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> RuleResponse:
    return RuleService(session).version(rule_id, current_user.id, current_user.role_codes, payload)
