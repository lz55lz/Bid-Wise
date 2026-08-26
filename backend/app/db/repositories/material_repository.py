from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    DocumentVersion,
    EnterpriseMaterial,
    Evidence,
    MatchResult,
    MaterialDocument,
)


class MaterialRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, material_id: UUID, *, for_update: bool = False) -> EnterpriseMaterial | None:
        statement = select(EnterpriseMaterial).where(
            EnterpriseMaterial.id == material_id, EnterpriseMaterial.deleted_at.is_(None)
        )
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_active(self, enterprise_id: UUID | None = None) -> list[EnterpriseMaterial]:
        statement = (
            select(EnterpriseMaterial)
            .where(EnterpriseMaterial.deleted_at.is_(None))
        )
        if enterprise_id:
            statement = statement.where(EnterpriseMaterial.enterprise_id == enterprise_id)
        statement = statement.order_by(EnterpriseMaterial.updated_at.desc(), EnterpriseMaterial.id)
        return list(self._session.scalars(statement))

    def list_confirmed(self, enterprise_id: UUID | None = None) -> list[EnterpriseMaterial]:
        statement = (
            select(EnterpriseMaterial)
            .where(
                EnterpriseMaterial.deleted_at.is_(None),
                EnterpriseMaterial.status == "CONFIRMED",
            )
        )
        if enterprise_id:
            statement = statement.where(EnterpriseMaterial.enterprise_id == enterprise_id)
        statement = statement.order_by(EnterpriseMaterial.updated_at.desc(), EnterpriseMaterial.id)
        return list(self._session.scalars(statement))

    def list_confirmed_for_enterprises(
        self, enterprise_ids: list[UUID]
    ) -> list[EnterpriseMaterial]:
        """多家企业(联合体)的已确认材料,enterprise_ids 为空时返回空列表。"""
        if not enterprise_ids:
            return []
        statement = (
            select(EnterpriseMaterial)
            .where(
                EnterpriseMaterial.deleted_at.is_(None),
                EnterpriseMaterial.status == "CONFIRMED",
                EnterpriseMaterial.enterprise_id.in_(enterprise_ids),
            )
            .order_by(EnterpriseMaterial.updated_at.desc(), EnterpriseMaterial.id)
        )
        return list(self._session.scalars(statement))

    def list_active_for_enterprises(
        self, enterprise_ids: list[UUID]
    ) -> list[EnterpriseMaterial]:
        """多家企业(联合体)的全部有效材料,enterprise_ids 为空时返回空列表。"""
        if not enterprise_ids:
            return []
        statement = (
            select(EnterpriseMaterial)
            .where(
                EnterpriseMaterial.deleted_at.is_(None),
                EnterpriseMaterial.enterprise_id.in_(enterprise_ids),
            )
            .order_by(EnterpriseMaterial.updated_at.desc(), EnterpriseMaterial.id)
        )
        return list(self._session.scalars(statement))

    def soft_delete(self, material_id: UUID) -> bool:
        material = self.get(material_id, for_update=True)
        if not material:
            return False
        from datetime import UTC, datetime
        material.deleted_at = datetime.now(UTC)
        self._session.flush()
        return True

    def add(self, material: EnterpriseMaterial) -> None:
        self._session.add(material)

    def add_document(self, material_document: MaterialDocument) -> None:
        self._session.add(material_document)

    def list_document_versions(self, material_id: UUID) -> list[DocumentVersion]:
        statement = (
            select(DocumentVersion)
            .join(MaterialDocument, MaterialDocument.document_version_id == DocumentVersion.id)
            .where(MaterialDocument.material_id == material_id)
            .order_by(DocumentVersion.created_at.desc())
        )
        return list(self._session.scalars(statement))

    def list_evidence_ids(self, material_id: UUID) -> list[UUID]:
        statement = (
            select(Evidence.id)
            .join(DocumentVersion, DocumentVersion.id == Evidence.document_version_id)
            .join(MaterialDocument, MaterialDocument.document_version_id == DocumentVersion.id)
            .where(MaterialDocument.material_id == material_id)
            .order_by(Evidence.id)
        )
        return list(self._session.scalars(statement))

    def list_declaration_status(self, material_ids: list[UUID]) -> dict[UUID, str]:
        """Return the strongest proof relation attached to each material.

        Priority: PROOF（有真实扫描件） > DECLARED（自声明） > None。
        """
        if not material_ids:
            return {}
        statement = select(
            MaterialDocument.material_id, MaterialDocument.relation
        ).where(MaterialDocument.material_id.in_(material_ids))
        priority = {"PROOF": 2, "DECLARED": 1}
        result: dict[UUID, str] = {}
        for material_id, relation in self._session.execute(statement).tuples():
            current = priority.get(result.get(material_id, ""), 0)
            incoming = priority.get(relation, 0)
            if incoming > current:
                result[material_id] = relation
        return result

    def list_evidence_ids_for_materials(
        self, material_ids: list[UUID]
    ) -> dict[UUID, list[UUID]]:
        """Batch fetch evidence IDs for multiple materials."""
        if not material_ids:
            return {}
        statement = (
            select(MaterialDocument.material_id, Evidence.id)
            .join(DocumentVersion, DocumentVersion.id == Evidence.document_version_id)
            .join(MaterialDocument, MaterialDocument.document_version_id == DocumentVersion.id)
            .where(MaterialDocument.material_id.in_(material_ids))
            .order_by(MaterialDocument.material_id, Evidence.id)
        )
        rows = self._session.execute(statement).tuples().all()
        result: dict[UUID, list[UUID]] = {mid: [] for mid in material_ids}
        for material_id, evidence_id in rows:
            result[material_id].append(evidence_id)
        return result

    def is_document_version_visible_for_project(
        self, project_id: UUID, document_version_id: UUID
    ) -> bool:
        statement = (
            select(MatchResult.id)
            .join(MaterialDocument, MaterialDocument.material_id == MatchResult.material_id)
            .join(EnterpriseMaterial, EnterpriseMaterial.id == MaterialDocument.material_id)
            .where(
                MatchResult.project_id == project_id,
                MatchResult.is_current.is_(True),
                MaterialDocument.document_version_id == document_version_id,
                EnterpriseMaterial.status == "CONFIRMED",
                EnterpriseMaterial.deleted_at.is_(None),
            )
            .limit(1)
        )
        return self._session.scalar(statement) is not None
