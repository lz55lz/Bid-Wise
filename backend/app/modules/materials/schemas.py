from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field


class MaterialUpsertRequest(BaseModel):
    enterprise_id: UUID
    material_type: str
    name: str = Field(min_length=1, max_length=256)
    material_no: str | None = None
    issuer: str | None = None
    level: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    amount: Decimal | None = None
    currency: str | None = None
    attributes: dict[str, object] = Field(
        default_factory=dict,
        description=(
            "演示用补充事实。使用 tag_codes 声明该材料可证明的招标资格标签，"
            '例如 {"tag_codes": ["QUAL_QUALIFICATION"]}；'
            "匹配不根据名称相似度猜测。"
        ),
    )


class MaterialResponse(MaterialUpsertRequest):
    id: UUID
    status: str
    created_at: datetime
    updated_at: datetime
