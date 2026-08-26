from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Document, DocumentNode, DocumentVersion


class DocumentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_document(self, document_id: UUID) -> Document | None:
        return self._session.get(Document, document_id)

    def list_by_project(self, project_id: UUID) -> list[Document]:
        statement = (
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.document_type == "TENDER",
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc())
        )
        return list(self._session.scalars(statement))

    def get_version(self, version_id: UUID) -> DocumentVersion | None:
        return self._session.get(DocumentVersion, version_id)

    def get_version_for_update(self, version_id: UUID) -> DocumentVersion | None:
        return self._session.scalar(
            select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update()
        )

    def get_by_project_logical_name_for_update(
        self, project_id: UUID, document_type: str, logical_name: str
    ) -> Document | None:
        return self._session.scalar(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.document_type == document_type,
                Document.logical_name == logical_name,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
        )

    def get_enterprise_by_logical_name_for_update(self, logical_name: str) -> Document | None:
        return self._session.scalar(
            select(Document)
            .where(
                Document.project_id.is_(None),
                Document.document_type == "ENTERPRISE",
                Document.logical_name == logical_name,
                Document.deleted_at.is_(None),
            )
            .with_for_update()
        )

    def next_version_no(self, document_id: UUID) -> int:
        value = self._session.scalar(
            select(func.max(DocumentVersion.version_no)).where(
                DocumentVersion.document_id == document_id
            )
        )
        return int(value or 0) + 1

    def add_document(self, document: Document) -> None:
        self._session.add(document)

    def add_version(self, version: DocumentVersion) -> None:
        self._session.add(version)

    def list_versions(self, document_id: UUID) -> list[DocumentVersion]:
        statement = (
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document_id)
            .order_by(DocumentVersion.version_no.desc())
        )
        return list(self._session.scalars(statement))

    def list_versions_by_ids(self, version_ids: list[UUID]) -> dict[UUID, DocumentVersion]:
        """Batch fetch versions by IDs. Returns {version_id: DocumentVersion}."""
        if not version_ids:
            return {}
        statement = select(DocumentVersion).where(DocumentVersion.id.in_(version_ids))
        rows = self._session.scalars(statement).all()
        return {v.id: v for v in rows}

    def list_documents_by_ids(self, document_ids: list[UUID]) -> dict[UUID, Document]:
        """Batch fetch documents by IDs. Returns {document_id: Document}."""
        if not document_ids:
            return {}
        statement = select(Document).where(Document.id.in_(document_ids))
        rows = self._session.scalars(statement).all()
        return {d.id: d for d in rows}

    def get_version_by_no(self, document_id: UUID, version_no: int) -> DocumentVersion | None:
        return self._session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_no == version_no,
            )
        )

    def list_nodes(self, version_id: UUID, offset: int, limit: int) -> list[DocumentNode]:
        statement = (
            select(DocumentNode)
            .where(
                DocumentNode.document_version_id == version_id,
                func.coalesce(
                    DocumentNode.metadata_["rechunk_superseded"].astext, "false"
                ) != "true",
            )
            .order_by(DocumentNode.order_no)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def list_nodes_candidates(
        self, version_id: UUID, offset: int, limit: int
    ) -> list[DocumentNode]:
        """只返回招标要求候选节点，SQL 层面过滤。"""
        statement = (
            select(DocumentNode)
            .where(
                DocumentNode.document_version_id == version_id,
                DocumentNode.tender_req_candidate == True,
                func.coalesce(
                    DocumentNode.metadata_["rechunk_superseded"].astext, "false"
                ) != "true",
            )
            .order_by(DocumentNode.order_no)
            .offset(offset)
            .limit(limit)
        )
        return list(self._session.scalars(statement))

    def add_nodes(self, nodes: list[DocumentNode]) -> None:
        self._session.add_all(nodes)
