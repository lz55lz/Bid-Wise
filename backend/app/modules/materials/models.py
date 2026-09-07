# ruff: noqa: E501
"""企业材料事实。"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EnterpriseMaterial(Base):
    """可用于资格匹配的企业自有材料；确认前不得作为投标证明。"""

    __tablename__ = "enterprise_materials"
    __table_args__ = (
        CheckConstraint(
            "material_type IN ('QUALIFICATION', 'CERTIFICATE', 'PERSONNEL', 'PROJECT_EXPERIENCE', 'FINANCE', 'OTHER')",
            name="enterprise_materials_type_check",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'CONFIRMED', 'ARCHIVED')", name="enterprise_materials_status_check"
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # 材料必须归属一个已维护的企业，匹配时再通过项目-企业绑定限定范围。
    enterprise_id: Mapped[UUID] = mapped_column(
        ForeignKey("enterprises.id"), nullable=False, index=True
    )
    material_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    material_no: Mapped[str | None] = mapped_column(String(128), index=True)
    issuer: Mapped[str | None] = mapped_column(String(256))
    level: Mapped[str | None] = mapped_column(String(128))
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, index=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    currency: Mapped[str | None] = mapped_column(String(16))
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
