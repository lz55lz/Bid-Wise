"""Evidence 领域仓储：只负责证据记录的持久化。"""

from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.modules.documents.models import DocumentNode, DocumentVersion, ProjectDocument
from app.modules.evidence.models import Evidence
from app.modules.retrieval.models import EvidenceEmbedding


class EvidenceRepository:
    """Evidence 数据访问入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _current_evidence_predicate():
        """当前业务可见 Evidence：非文档证据 + READY 的当前文档版本。

        历史文档 Evidence 会永久保留用于审计血缘，但任何“当前业务计算”都必须
        经过这一谓词，不能因为保留历史而重新混入 RAG、发现项分析或索引任务。
        """
        return or_(
            Evidence.document_version_id.is_(None),
            and_(
                ProjectDocument.current_version_id == Evidence.document_version_id,
                DocumentVersion.parse_status == "READY",
            ),
        )

    def add_many(self, evidences: list[Evidence]) -> None:
        """批量登记同一版本产生的证据，提交由上层解析事务统一控制。"""
        self._session.add_all(evidences)

    async def get_in_project(self, project_id: UUID, evidence_id: UUID) -> Evidence | None:
        """按项目和 Evidence 主键同时查询，禁止先按 UUID 取对象再补授权。"""
        return await self._session.scalar(
            select(Evidence).where(
                Evidence.project_id == project_id,
                Evidence.id == evidence_id,
            )
        )

    async def list_by_ids_in_project(
        self, project_id: UUID, evidence_ids: list[UUID]
    ) -> list[Evidence]:
        """只返回当前项目中的指定 Evidence，避免请求体 UUID 形成跨项目关联。"""
        if not evidence_ids:
            return []
        statement = select(Evidence).where(
            Evidence.project_id == project_id,
            Evidence.id.in_(evidence_ids),
        )
        return list((await self._session.scalars(statement)).all())

    async def list_current_by_ids_in_project(
        self, project_id: UUID, evidence_ids: list[UUID]
    ) -> list[Evidence]:
        """按 ID 回查当前业务可见 Evidence；用于异步任务执行前确认冻结输入仍有效。"""
        if not evidence_ids:
            return []
        statement = (
            select(Evidence)
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .where(
                Evidence.project_id == project_id,
                Evidence.id.in_(evidence_ids),
                self._current_evidence_predicate(),
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def list_for_finding_analysis(self, project_id: UUID) -> list[Evidence]:
        """返回当前项目全部有效 Evidence，供后台分批发现项分析。

        LLM 客户端自身按字符预算分批并有限并发，因此这里不能再任意截取“前 24 条”；
        那会让长招标文件后半段永远没有被分析的机会。
        """
        order_start = Evidence.locator["order_start"].as_integer()
        statement = (
            select(Evidence)
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .where(
                Evidence.project_id == project_id,
                self._current_evidence_predicate(),
            )
            .order_by(
                Evidence.document_version_id.asc().nulls_last(),
                order_start.asc().nulls_last(),
                Evidence.created_at,
                Evidence.id,
            )
        )
        return list((await self._session.scalars(statement)).all())

    async def list_ids_by_document_version(self, document_version_id: UUID) -> list[UUID]:
        """在节点级联删除前取出受影响 Evidence ID，供发现项失效处理使用。"""
        statement = select(Evidence.id).where(Evidence.document_version_id == document_version_id)
        return list((await self._session.scalars(statement)).all())

    async def list_unindexed_by_project(self, project_id: UUID) -> list[Evidence]:
        """只为当前业务可见 Evidence 重建缺失向量；历史版本不重复消耗 embedding。"""
        statement = (
            select(Evidence)
            .outerjoin(EvidenceEmbedding, EvidenceEmbedding.evidence_id == Evidence.id)
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .where(
                Evidence.project_id == project_id,
                EvidenceEmbedding.evidence_id.is_(None),
                self._current_evidence_predicate(),
            )
            .order_by(Evidence.created_at, Evidence.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_unindexed_by_ids(
        self, project_id: UUID, evidence_ids: list[UUID]
    ) -> list[Evidence]:
        """Worker 仅索引任务快照中仍属于当前业务版本且缺少向量的 Evidence。"""
        if not evidence_ids:
            return []
        statement = (
            select(Evidence)
            .outerjoin(EvidenceEmbedding, EvidenceEmbedding.evidence_id == Evidence.id)
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .where(
                Evidence.project_id == project_id,
                Evidence.id.in_(evidence_ids),
                EvidenceEmbedding.evidence_id.is_(None),
                self._current_evidence_predicate(),
            )
            .order_by(Evidence.created_at, Evidence.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def list_search_candidates(self, project_id, vector: list[float], limit: int):
        """在 SQL 层召回，并仅为模型回取命中子节点的父章节上下文。"""
        child = aliased(DocumentNode)
        parent = aliased(DocumentNode)
        distance = EvidenceEmbedding.embedding.cosine_distance(vector)
        statement = (
            # 标题父节点通常因过短而不进入 cleaned_content；上下文可安全回退到
            # 原始标题文本。它不作为 Evidence 返回，故不会改变引用边界。
            select(
                Evidence,
                distance.label("distance"),
                func.coalesce(parent.cleaned_content, parent.content),
            )
            .join(EvidenceEmbedding, EvidenceEmbedding.evidence_id == Evidence.id)
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .outerjoin(child, Evidence.document_node_id == child.id)
            .outerjoin(parent, child.parent_node_id == parent.id)
            .where(
                Evidence.project_id == project_id,
                self._current_evidence_predicate(),
            )
            .order_by(distance, Evidence.id)
            .limit(limit)
        )
        return list((await self._session.execute(statement)).all())

    async def list_bm25_search_candidates(self, project_id, query: str, limit: int):
        """项目内 zhparser BM25 候选，与向量候选共同参与 RRF。

        Evidence 是已完成授权和语义聚合的检索单元，因此关键词通路也必须从
        Evidence 出发，不能退回到对象键或未授权的原始节点。
        """
        child = aliased(DocumentNode)
        parent = aliased(DocumentNode)
        zh_config = literal_column("'zh'::regconfig")
        tsquery = func.plainto_tsquery(zh_config, query.strip())
        lexical_text = (
            func.coalesce(Evidence.locator["document_name"].astext, "")
            + literal_column("' '")
            + func.coalesce(Evidence.locator["section_path"].astext, "")
            + literal_column("' '")
            + func.coalesce(Evidence.quoted_text, "")
        )
        search_vector = func.to_tsvector(zh_config, lexical_text)
        rank = func.ts_rank_cd(search_vector, tsquery).label("rank")
        statement = (
            select(Evidence, rank, func.coalesce(parent.cleaned_content, parent.content))
            .outerjoin(DocumentVersion, Evidence.document_version_id == DocumentVersion.id)
            .outerjoin(ProjectDocument, DocumentVersion.document_id == ProjectDocument.id)
            .outerjoin(child, Evidence.document_node_id == child.id)
            .outerjoin(parent, child.parent_node_id == parent.id)
            .where(
                Evidence.project_id == project_id,
                self._current_evidence_predicate(),
                search_vector.op("@@")(tsquery),
            )
            .order_by(rank.desc(), Evidence.id)
            .limit(limit)
        )
        return list((await self._session.execute(statement)).all())

    async def list_for_document_version(self, project_id, document_version_id) -> list[Evidence]:
        """按原始节点顺序返回一个版本的 Evidence，用于命中邻居扩展。"""
        order_start = Evidence.locator["order_start"].as_integer()
        statement = (
            select(Evidence)
            .where(
                Evidence.project_id == project_id,
                Evidence.document_version_id == document_version_id,
            )
            .order_by(order_start, Evidence.id)
        )
        return list((await self._session.scalars(statement)).all())
