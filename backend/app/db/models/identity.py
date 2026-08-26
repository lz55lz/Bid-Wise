from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256))
    is_system: Mapped[bool] = mapped_column(default=True)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("app.roles.code"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenderProject(Base):
    __tablename__ = "tender_projects"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256))
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    purchaser: Mapped[str] = mapped_column(String(256))
    project_type: Mapped[str] = mapped_column(String(128))
    region: Mapped[str] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    bid_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT")
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "role_code"),)

    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("app.roles.code"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProjectEnterprise(Base):
    """项目-投标企业绑定(联合体):一个项目可绑多家企业,is_lead 标记主投标人。"""

    __tablename__ = "project_enterprises"

    project_id: Mapped[UUID] = mapped_column(ForeignKey("app.tender_projects.id"), primary_key=True)
    enterprise_id: Mapped[UUID] = mapped_column(ForeignKey("app.enterprises.id"), primary_key=True)
    is_lead: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)


class Enterprise(Base):
    __tablename__ = "enterprises"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    credit_code: Mapped[str | None] = mapped_column(String(18))  # 统一社会信用代码
    enterprise_type: Mapped[str | None] = mapped_column(String(32))  # 母公司/子公司/独立公司
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    members: Mapped[list["EnterpriseMember"]] = relationship(
        "EnterpriseMember", back_populates="enterprise", lazy="selectin"
    )


class EnterpriseMember(Base):
    __tablename__ = "enterprise_members"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    enterprise_id: Mapped[UUID] = mapped_column(ForeignKey("app.enterprises.id"), nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("app.users.id"), nullable=False)
    role_code: Mapped[str] = mapped_column(String(32), nullable=False)  # ADMIN/MEMBER
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    enterprise: Mapped["Enterprise"] = relationship("Enterprise", back_populates="members")
    user: Mapped["User"] = relationship("User", lazy="selectin")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.users.id"))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[UUID | None] = mapped_column(nullable=True)
    project_id: Mapped[UUID | None] = mapped_column(ForeignKey("app.tender_projects.id"))
    request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    before_summary: Mapped[str | None] = mapped_column(Text)
    after_summary: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
