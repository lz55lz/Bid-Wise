from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_manage_enterprise_materials
from app.db.models import DocumentNode, Evidence, KnowledgeVersion
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.evidence_repository import EvidenceRepository
from app.db.repositories.material_repository import MaterialRepository
from app.schemas.evidences import EvidenceResponse
from app.services.project_service import ProjectService

_SOURCE_TYPES = {
    "SECTION": "DOCUMENT_SECTION",
    "TABLE": "DOCUMENT_TABLE",
    "CELL": "DOCUMENT_TABLE",
    "IMAGE": "DOCUMENT_IMAGE",
    "PARAGRAPH": "DOCUMENT_TEXT",
    "LIST": "DOCUMENT_TEXT",
}
_MAX_QUOTE_CHARS = 1_000


class EvidenceService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._documents = DocumentRepository(session)
        self._evidences = EvidenceRepository(session)
        self._materials = MaterialRepository(session)
        self._projects = ProjectService(session)

    def create_document_evidences(
        self, nodes: list[DocumentNode], created_by: UUID
    ) -> list[Evidence]:
        evidences = [
            Evidence(
                id=uuid4(),
                source_type=_SOURCE_TYPES[node.node_type],
                document_version_id=node.document_version_id,
                document_node_id=node.id,
                page_number=node.page_number,
                quoted_text=self._clip_for_citation(node.content),
                content_hash=node.content_hash,
                bbox=node.bbox,
                source_reference={},
                created_at=datetime.now(UTC),
                created_by=created_by,
            )
            for node in nodes
        ]
        self._evidences.add_all(evidences)
        return evidences

    def get_visible(
        self, evidence_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> EvidenceResponse:
        evidence = self._evidences.get(evidence_id)
        if (
            evidence is None
            or evidence.document_version_id is None
            or evidence.document_node_id is None
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        version = self._documents.get_version(evidence.document_version_id)
        if version is None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        document = self._documents.get_document(version.document_id)
        if document is None or document.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        if document.document_type == "TENDER" and document.project_id is not None:
            self._projects.get_visible(document.project_id, actor_id, role_codes)
        elif document.document_type in {"LEGAL", "CASE"}:
            published = self._session.scalar(
                select(KnowledgeVersion.id).where(
                    KnowledgeVersion.source_document_version_id == version.id,
                    KnowledgeVersion.status == "PUBLISHED",
                )
            )
            if published is None:
                raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        elif document.document_type != "ENTERPRISE" or not can_manage_enterprise_materials(
            role_codes
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return EvidenceResponse(
            id=evidence.id,
            source_type=evidence.source_type,
            document_id=document.id,
            document_version_id=version.id,
            document_node_id=evidence.document_node_id,
            file_name=version.file_name,
            version_no=version.version_no,
            page_number=evidence.page_number,
            quoted_text=evidence.quoted_text,
            content_hash=evidence.content_hash,
            bbox=evidence.bbox,
        )

    def get_visible_for_project(
        self, evidence_id: UUID, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> EvidenceResponse:
        """Resolve a RAG citation through a project-scoped PostgreSQL authorization check."""
        evidence = self._evidences.get(evidence_id)
        if (
            evidence is None
            or evidence.document_version_id is None
            or evidence.document_node_id is None
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "Evidence does not exist", 404)
        version = self._documents.get_version(evidence.document_version_id)
        document = None if version is None else self._documents.get_document(version.document_id)
        if version is None or document is None or document.deleted_at is not None:
            raise DomainError("RESOURCE_NOT_FOUND", "Evidence does not exist", 404)
        self._projects.get_visible(project_id, actor_id, role_codes)
        if document.document_type == "TENDER" and document.project_id == project_id:
            return self._response(evidence, document.id, version)
        if (
            document.document_type == "ENTERPRISE"
            and self._materials.is_document_version_visible_for_project(project_id, version.id)
        ):
            return self._response(evidence, document.id, version)
        raise DomainError("RESOURCE_NOT_FOUND", "Evidence does not exist", 404)

    @staticmethod
    def _response(evidence: Evidence, document_id: UUID, version) -> EvidenceResponse:
        return EvidenceResponse(
            id=evidence.id,
            source_type=evidence.source_type,
            document_id=document_id,
            document_version_id=version.id,
            document_node_id=evidence.document_node_id,
            file_name=version.file_name,
            version_no=version.version_no,
            page_number=evidence.page_number,
            quoted_text=evidence.quoted_text,
            content_hash=evidence.content_hash,
            bbox=evidence.bbox,
        )

    @staticmethod
    def _clip_for_citation(content: str) -> str:
        content = content.strip()
        if len(content) <= _MAX_QUOTE_CHARS:
            return content
        return f"{content[: _MAX_QUOTE_CHARS - 1]}…"
