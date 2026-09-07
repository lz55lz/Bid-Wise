"""风险规则模板的 HTTP 输入输出模型。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class RiskRuleTemplateCreateRequest(BaseModel):
    """新建自定义风险规则模板；定义会被服务层按受限 DSL 再校验。"""

    code: str = Field(min_length=2, max_length=80, pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str = Field(min_length=1, max_length=256)
    risk_type: str = Field(min_length=2, max_length=32)
    severity: str = Field(min_length=2, max_length=16)
    definition: dict[str, Any]
    is_enabled: bool = True


class RiskRuleTemplateVersionRequest(BaseModel):
    """更新模板时创建一个新版本，历史版本不原地修改。"""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    risk_type: str | None = Field(default=None, min_length=2, max_length=32)
    severity: str = Field(min_length=2, max_length=16)
    definition: dict[str, Any]
    is_enabled: bool = True


class RiskRuleVersionResponse(BaseModel):
    id: UUID
    version_no: int
    severity: str
    definition: dict[str, Any]
    is_enabled: bool
    effective_at: datetime
    retired_at: datetime | None
    created_at: datetime
    created_by: UUID


class RiskRuleTemplateResponse(BaseModel):
    id: UUID
    code: str
    name: str
    risk_type: str
    active_version: RiskRuleVersionResponse | None
