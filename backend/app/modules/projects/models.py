"""项目域 ORM 模型。

项目成员是资源访问授权的唯一事实来源：除 SYSTEM_ADMIN 外，任何用户访问项目、
文件、Evidence 或报告前，都必须先从 project_members 回查成员关系。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class TenderProject(Base):
    """一项投标工作及其生命周期元数据。"""

    __tablename__ = "tender_projects"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'ARCHIVED')",
            name="tender_projects_status_check",
        ),
        CheckConstraint(
            "bid_deadline_source IS NULL OR bid_deadline_source IN ('MANUAL', 'PIPELINE')",
            name="tender_projects_bid_deadline_source_check",
        ),
        UniqueConstraint("code"),
        Index("ix_tender_projects_code", "code"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    code: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    purchaser: Mapped[str] = mapped_column(String(256), nullable=False)
    project_type: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str] = mapped_column(String(128), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="CNY")
    bid_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # MANUAL 表示用户明确维护，PIPELINE 表示来自最新人工确认的招标文件事实。
    # 新版文件只能覆盖 PIPELINE 值，不能静默覆盖用户手工修改。
    bid_deadline_source: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectMember(Base):
    """项目级成员关系。

    role 只允许 OWNER、EDITOR、VIEWER 三种业务含义，由服务层校验；同一用户在同一
    项目只能有一条成员记录，角色变更是更新而非插入重复授权。
    """

    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('OWNER', 'EDITOR', 'VIEWER')",
            name="project_members_role_check",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(ForeignKey("tender_projects.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Enterprise(Base):
    """可被投标项目选用的企业主体。

    这是投标业务数据，不是租户边界：一个私有部署可以维护多家关联企业，并可在一个
    项目中选择一家或多家企业（联合体）。资源访问仍只以 ``project_members`` 为准。
    """

    __tablename__ = "enterprises"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    credit_code: Mapped[str | None] = mapped_column(String(18), unique=True)
    enterprise_type: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class EnterpriseMember(Base):
    """企业资料协作者；项目、文件、Evidence 的授权仍只看 project_members。"""

    __tablename__ = "enterprise_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('ADMIN', 'EDITOR', 'VIEWER')", name="enterprise_members_role_check"
        ),
        UniqueConstraint("enterprise_id", "user_id"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enterprise_id: Mapped[UUID] = mapped_column(ForeignKey("enterprises.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectEnterprise(Base):
    """项目与投标企业的绑定；``is_lead`` 表示联合体牵头方。"""

    __tablename__ = "project_enterprises"

    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id", ondelete="CASCADE"), primary_key=True
    )
    enterprise_id: Mapped[UUID] = mapped_column(ForeignKey("enterprises.id"), primary_key=True)
    is_lead: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
