from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.base import ApiSchema

MaterialType = Literal["QUALIFICATION", "CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"]
ReviewStatus = Literal["PENDING", "CONFIRMED", "REJECTED"]


class EnterpriseMaterialCreate(ApiSchema):
    # 归属企业用于项目材料匹配；保留可空值以兼容历史全局材料。
    enterprise_id: UUID | None = None
    material_type: MaterialType
    name: str = Field(min_length=1, max_length=512)
    material_no: str | None = Field(default=None, max_length=128)
    issuer: str | None = Field(default=None, max_length=256)
    level: str | None = Field(default=None, max_length=128)
    valid_from: date | None = None
    valid_to: date | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="CNY", pattern=r"^[A-Z]{3}$")
    attributes: dict[str, Any] = Field(default_factory=dict)
    # 页面表单由当前用户确认真实性时，可作为人工声明材料参与匹配；附件为可选增强证据。
    self_declared: bool = False

    @field_validator("attributes")
    @classmethod
    def attributes_must_be_an_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 100:
            raise ValueError("attributes has too many keys")
        return value


class EnterpriseMaterialUpdate(ApiSchema):
    name: str | None = Field(default=None, min_length=1, max_length=512)
    material_no: str | None = Field(default=None, max_length=128)
    issuer: str | None = Field(default=None, max_length=256)
    level: str | None = Field(default=None, max_length=128)
    valid_from: date | None = None
    valid_to: date | None = None
    amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=2)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    attributes: dict[str, Any] | None = None
    status: ReviewStatus | None = None


class MaterialDocumentAttachRequest(ApiSchema):
    """将已解析的企业证明文件关联到另一条材料。"""

    document_id: UUID
    document_version_id: UUID


class MaterialDocumentResponse(ApiSchema):
    document_id: UUID
    document_version_id: UUID
    file_name: str
    version_no: int
    parse_status: str


class EnterpriseMaterialResponse(ApiSchema):
    id: UUID
    enterprise_id: UUID | None
    material_type: MaterialType
    name: str
    material_no: str | None
    issuer: str | None
    level: str | None
    valid_from: date | None
    valid_to: date | None
    amount: Decimal | None
    currency: str
    attributes: dict[str, Any]
    status: ReviewStatus
    evidence_ids: list[UUID]
    documents: list[MaterialDocumentResponse]
    created_at: datetime
    updated_at: datetime
