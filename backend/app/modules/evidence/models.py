"""Evidence 证据模型。

Evidence 是模型回答、分析结论和报告引用的授权锚点。向量检索只负责候选召回；返回
Evidence 前必须用 project_id 回查项目成员权限，不能相信对象键或向量元数据。
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Evidence(Base):
    """可被引用的语义证据单元。

    ``document_node_id`` 仅保留为兼容/快速定位的 anchor；当一个结构化 Evidence 由
    多个原始节点合并而来时，正式血缘以 ``EvidenceSourceNode`` 为准。
    """

    __tablename__ = "evidences"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('DOCUMENT_NODE', 'USER_CONFIRMATION', 'SYSTEM_RULE')",
            name="evidences_source_type_check",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    project_id: Mapped[UUID] = mapped_column(
        ForeignKey("tender_projects.id"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    document_version_id: Mapped[UUID | None] = mapped_column(ForeignKey("document_versions.id"))
    document_node_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("document_nodes.id", ondelete="CASCADE"), index=True
    )
    quoted_text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    locator: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(ForeignKey("users.id"))


class EvidenceSourceNode(Base):
    """Evidence 与原始 DocumentNode 的正式多对多血缘。

    结构化切块允许多个相邻节点合并为一个 Evidence，因此不能再把 anchor node
    当成完整来源。ordinal 保留原文顺序，供条款映射、人工审核引用和审计使用。
    """

    __tablename__ = "evidence_source_nodes"
    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"), primary_key=True
    )
    document_node_id: Mapped[UUID] = mapped_column(
        ForeignKey("document_nodes.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
