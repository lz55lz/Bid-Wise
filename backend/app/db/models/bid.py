"""Bid tag system models - 标签体系 + 文档 + 风险 + 报告

12 张表全部在 public schema（由 migration 202608310001 创建）。
"""
from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class BidTagCategory(Base):
    """标签分类：CAT01-CAT11"""
    __tablename__ = "bid_tag_category"

    category_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    category_name: Mapped[str] = mapped_column(String(100), nullable=False)
    category_desc: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    tags: Mapped[list["BidTagDict"]] = relationship(back_populates="category")


class BidTagLevel(Base):
    """标签优先级：P0/P1/P2"""
    __tablename__ = "bid_tag_level"

    level_code: Mapped[str] = mapped_column(String(10), primary_key=True)
    level_name: Mapped[str] = mapped_column(String(50), nullable=False)
    level_desc: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(default=0)


class BidTagDict(Base):
    """150 个标签字典"""
    __tablename__ = "bid_tag_dict"

    tag_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tag_code: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    tag_name: Mapped[str] = mapped_column(String(200), nullable=False)
    tag_value: Mapped[str | None] = mapped_column(String(500))
    category_code: Mapped[str | None] = mapped_column(String(20), ForeignKey("app.bid_tag_category.category_code"))
    level_code: Mapped[str | None] = mapped_column(String(10), ForeignKey("app.bid_tag_level.level_code"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    data_type: Mapped[str] = mapped_column(String(20), default="str")
    extraction_prompt: Mapped[str | None] = mapped_column(Text)
    value_example: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(10), default="P1")

    category: Mapped["BidTagCategory"] = relationship(back_populates="tags")


class BidTagRelation(Base):
    """标签关系：CONSTRAINS/DEPENDS_ON/TRIGGERS/BEFORE/EQUAL_OR_BEFORE/COMPOSES"""
    __tablename__ = "bid_tag_relation"

    relation_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_tag_code: Mapped[str] = mapped_column(String(80), nullable=False)
    target_tag_code: Mapped[str] = mapped_column(String(80), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    relation_desc: Mapped[str | None] = mapped_column(Text)
    rule_json: Mapped[dict | None] = mapped_column(JSONB)
    priority: Mapped[str] = mapped_column(String(10), default="P1")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class BidDocument(Base):
    """招标文件文档"""
    __tablename__ = "bid_document"

    doc_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_name: Mapped[str] = mapped_column(String(500), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(30))
    doc_url: Mapped[str | None] = mapped_column(Text)
    project_id: Mapped[str | None] = mapped_column(String(36))  # UUID as string
    file_hash: Mapped[str | None] = mapped_column(String(64))
    parse_status: Mapped[str] = mapped_column(String(20), default="pending")
    raw_text_path: Mapped[str | None] = mapped_column(Text)


class BidDocChunk(Base):
    """文档分块，含双层标签 + 1024维向量"""
    __tablename__ = "bid_doc_chunk"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_bid_doc_chunk_doc_index"),
    )

    chunk_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app.bid_document.doc_id"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[str | None] = mapped_column(Text)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_type: Mapped[str] = mapped_column(String(20), default="paragraph")
    category_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    candidate_tags: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    prev_chunk_id: Mapped[int | None] = mapped_column(BigInteger)
    next_chunk_id: Mapped[int | None] = mapped_column(BigInteger)

    # embedding 由 migration 通过 vector(1024) 类型管理
    # ORM 层用 Text 读取，不做向量运算


class BidDocumentTag(Base):
    """文档标签提取结果（键：document_version_id）"""
    __tablename__ = "bid_document_tag"
    __table_args__ = (
        UniqueConstraint("version_id", "tag_id", name="uq_bid_doc_tag_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tag_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("app.bid_tag_dict.tag_id"), nullable=False)
    tag_value: Mapped[str | None] = mapped_column(Text)
    tag_value_json: Mapped[dict | None] = mapped_column(JSONB)
    source_text: Mapped[str | None] = mapped_column(Text)
    source_node_id: Mapped[str | None] = mapped_column(String(64))
    source_page: Mapped[int | None] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 2))
    extract_method: Mapped[str | None] = mapped_column(String(20))
    llm_model: Mapped[str | None] = mapped_column(String(50))
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=None)
    reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    reviewer: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    remark: Mapped[str | None] = mapped_column(Text)


class BidTaskLog(Base):
    """管线任务日志"""
    __tablename__ = "bid_task_log"

    task_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[str | None] = mapped_column(String(100))
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    node_name: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_summary: Mapped[str | None] = mapped_column(Text)
    output_summary: Mapped[str | None] = mapped_column(Text)
    error_msg: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EnterpriseProfile(Base):
    """企业画像"""
    __tablename__ = "enterprise_profile"

    # ``enterprise_profile`` predates the UUID-based enterprise domain model.
    # Its database key remains the legacy auto-incrementing ``ep_id``; the
    # UUID association is carried by ``enterprise_id``.  Mapping ``id`` as the
    # primary key made SQLAlchemy explicitly insert NULL into ``ep_id`` and
    # broke the public enterprise-create API.
    ep_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    enterprise_id: Mapped[str | None] = mapped_column(String(36), unique=True)
    enterprise_name: Mapped[str] = mapped_column(String(200), nullable=False)
    credit_code: Mapped[str | None] = mapped_column(String(18), unique=True)
    enterprise_type: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str | None] = mapped_column(String(20))
    created_by: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    qualifications: Mapped[dict | None] = mapped_column(JSONB)
    past_projects: Mapped[dict | None] = mapped_column(JSONB)
    financials: Mapped[dict | None] = mapped_column(JSONB)
    personnel: Mapped[dict | None] = mapped_column(JSONB)
    awards: Mapped[dict | None] = mapped_column(JSONB)
    blacklist_status: Mapped[dict | None] = mapped_column(JSONB)


class CompetitorHistory(Base):
    """竞争对手历史投标记录"""
    __tablename__ = "competitor_history"

    comp_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    enterprise_name: Mapped[str] = mapped_column(String(200), nullable=False)
    project_name: Mapped[str | None] = mapped_column(String(500))
    project_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    bid_amount: Mapped[float | None] = mapped_column(Numeric(15, 2))
    win: Mapped[bool | None] = mapped_column(Boolean)
    bid_date: Mapped[datetime | None] = mapped_column(Date)
    project_type: Mapped[str | None] = mapped_column(String(50))
    region: Mapped[str | None] = mapped_column(String(50))
