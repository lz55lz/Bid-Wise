"""Evidence 的可重建向量索引模型。"""

from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

EMBEDDING_DIMENSIONS = 1024


class EvidenceEmbedding(Base):
    """Evidence 的 bge-m3 向量。

    向量是可从原始 Evidence 重新生成的派生数据，不是授权事实源；检索命中后仍必须
    回查 evidences.project_id 与 project_members。
    """

    __tablename__ = "evidence_embeddings"
    __table_args__ = (
        Index(
            "ix_evidence_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    evidence_id: Mapped[UUID] = mapped_column(
        ForeignKey("evidences.id", ondelete="CASCADE"),
        primary_key=True,
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
