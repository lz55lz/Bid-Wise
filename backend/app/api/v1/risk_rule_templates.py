"""风险规则模板维护接口。

项目风险接口只负责执行、查询和复核；模板维护属于系统级合规能力，因此单独路由，
避免普通项目成员借项目写权限改变所有项目的风险口径。
"""

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.risks.rule_schemas import (
    RiskRuleTemplateCreateRequest,
    RiskRuleTemplateResponse,
    RiskRuleTemplateVersionRequest,
)
from app.modules.risks.rule_service import RiskRuleTemplateService

router = APIRouter(prefix="/risk-rule-templates", tags=["风险规则模板"])


@router.get("", response_model=list[RiskRuleTemplateResponse])
async def list_templates(
    current_user: CurrentUser, session: DatabaseSession
) -> list[RiskRuleTemplateResponse]:
    return await RiskRuleTemplateService(session).list(current_user)


@router.post("", response_model=RiskRuleTemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    payload: RiskRuleTemplateCreateRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> RiskRuleTemplateResponse:
    return await RiskRuleTemplateService(session).create(
        current_user,
        code=payload.code,
        name=payload.name,
        risk_type=payload.risk_type,
        severity=payload.severity,
        definition=payload.definition,
        is_enabled=payload.is_enabled,
    )


@router.patch("/{rule_id}", response_model=RiskRuleTemplateResponse)
async def version_template(
    rule_id: UUID,
    payload: RiskRuleTemplateVersionRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> RiskRuleTemplateResponse:
    return await RiskRuleTemplateService(session).create_version(
        current_user,
        rule_id,
        name=payload.name,
        risk_type=payload.risk_type,
        severity=payload.severity,
        definition=payload.definition,
        is_enabled=payload.is_enabled,
    )
