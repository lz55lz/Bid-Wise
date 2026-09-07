# ruff: noqa: E501
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ProjectRisk(Base):
    __tablename__ = "project_risks"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')",
            name="project_risks_severity_check",
        ),
        CheckConstraint(
            "status IN ('OPEN', 'ACCEPTED', 'RESOLVED', 'DISMISSED')",
            name="project_risks_status_check",
        ),
        # 模板升级后必须保留旧版本命中的历史风险，不能用 rule_code 把它覆盖掉。
        UniqueConstraint(
            "project_id",
            "rule_version_id",
            "subject",
            name="uq_project_risks_rule_version_subject",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"), nullable=False, index=True
    )
    # 风险必须能回溯到实际执行的模板版本；内置兜底风险也会在首次扫描时初始化版本。
    rule_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("risk_rule_versions.id"), index=True
    )
    rule_code: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(256), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_data: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    resolution: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
