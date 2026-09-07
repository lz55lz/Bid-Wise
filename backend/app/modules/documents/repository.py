"""项目文档仓储：只执行文档与版本的持久化查询。"""

from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.documents.models import (
    DocumentNode,
    DocumentParseJob,
    DocumentVersion,
    ProjectDocument,
)


class DocumentRepository:
    """项目文档数据访问入口。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_document(self, document: ProjectDocument) -> None:
        self._session.add(document)

    def add_version(self, version: DocumentVersion) -> None:
        self._session.add(version)

    async def list_active_by_project(self, project_id: UUID) -> list[ProjectDocument]:
        statement = (
            select(ProjectDocument)
            .where(
                ProjectDocument.project_id == project_id,
                ProjectDocument.deleted_at.is_(None),
            )
            .order_by(ProjectDocument.created_at.desc(), ProjectDocument.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_current_version(self, document: ProjectDocument) -> DocumentVersion | None:
        if document.current_version_id is None:
            return None
        return await self._session.get(DocumentVersion, document.current_version_id)

    async def get_active(
        self, document_id: UUID, *, for_update: bool = False
    ) -> ProjectDocument | None:
        statement = select(ProjectDocument).where(
            ProjectDocument.id == document_id,
            ProjectDocument.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def get_version(self, version_id: UUID) -> DocumentVersion | None:
        return await self._session.get(DocumentVersion, version_id)

    async def get_version_for_document(
        self, document_id: UUID, version_id: UUID
    ) -> DocumentVersion | None:
        """按逻辑文档限定版本，不能将其他文档版本 UUID 直接带入当前项目操作。"""
        statement = select(DocumentVersion).where(
            DocumentVersion.id == version_id,
            DocumentVersion.document_id == document_id,
        )
        return await self._session.scalar(statement)

    async def get_version_by_number(
        self, document_id: UUID, version_no: int
    ) -> DocumentVersion | None:
        return await self._session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_no == version_no,
            )
        )

    async def list_versions(self, document_id: UUID) -> list[DocumentVersion]:
        """按版本号返回同一逻辑文档的全部不可变版本。"""
        statement = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc(), DocumentVersion.id)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_parse_job(self, version_id: UUID) -> DocumentParseJob | None:
        statement = select(DocumentParseJob).where(
            DocumentParseJob.document_version_id == version_id
        )
        return await self._session.scalar(statement)

    async def has_active_parse_for_document(self, document_id: UUID) -> bool:
        """同一逻辑文档同一时刻只允许一个解析任务，避免版本切换发生竞态。"""
        statement = (
            select(DocumentParseJob.id)
            .join(DocumentVersion, DocumentVersion.id == DocumentParseJob.document_version_id)
            .where(
                DocumentVersion.document_id == document_id,
                DocumentParseJob.status.in_(("QUEUED", "RUNNING")),
            )
            .limit(1)
        )
        return (await self._session.scalar(statement)) is not None

    async def get_parse_job_for_update(self, job_id: UUID) -> DocumentParseJob | None:
        statement = select(DocumentParseJob).where(DocumentParseJob.id == job_id).with_for_update()
        return await self._session.scalar(statement)

    async def list_parse_jobs_for_recovery(self, stale_before: datetime) -> list[DocumentParseJob]:
        statement = (
            select(DocumentParseJob)
            .where(
                (DocumentParseJob.status == "QUEUED")
                | (
                    (DocumentParseJob.status == "RUNNING")
                    & (DocumentParseJob.started_at.is_not(None))
                    & (DocumentParseJob.started_at < stale_before)
                )
            )
            .order_by(DocumentParseJob.created_at, DocumentParseJob.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.scalars(statement)).all())

    async def get_parse_job_by_id(self, job_id: UUID) -> DocumentParseJob | None:
        """按任务 ID 查询解析任务；项目与文档归属由服务层继续验证。"""
        return await self._session.get(DocumentParseJob, job_id)

    def add_parse_job(self, job: DocumentParseJob) -> None:
        self._session.add(job)

    async def delete_nodes(self, version_id: UUID) -> None:
        # staging 版本失败重试时会整体重建节点。先断开同版本内部的父子 FK，
        # 再批量删除，避免自引用树受 RESTRICT 约束影响；Evidence anchor 会按 FK
        # CASCADE 一并清理，本操作从不用于当前 READY 历史版本。
        await self._session.execute(
            update(DocumentNode)
            .where(DocumentNode.document_version_id == version_id)
            .values(parent_node_id=None)
        )
        await self._session.execute(
            delete(DocumentNode).where(DocumentNode.document_version_id == version_id)
        )

    async def list_nodes(
        self, version_id: UUID, offset: int, limit: int
    ) -> tuple[list[DocumentNode], int]:
        """节点按文档位置稳定排序，并与总数在同一版本范围内查询。"""
        statement = (
            select(DocumentNode)
            .where(DocumentNode.document_version_id == version_id)
            .order_by(DocumentNode.order_no, DocumentNode.id)
            .offset(offset)
            .limit(limit)
        )
        nodes = list((await self._session.scalars(statement)).all())
        total = await self._session.scalar(
            select(func.count())
            .select_from(DocumentNode)
            .where(DocumentNode.document_version_id == version_id)
        )
        return nodes, int(total or 0)

    def add_nodes(self, nodes: list[DocumentNode]) -> None:
        self._session.add_all(nodes)
