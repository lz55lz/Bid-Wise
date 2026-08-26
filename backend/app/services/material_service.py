from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_manage_enterprise_materials
from app.db.models import Enterprise, EnterpriseMaterial, MaterialDocument
from app.db.repositories.document_repository import DocumentRepository
from app.db.repositories.material_repository import MaterialRepository
from app.schemas.materials import (
    EnterpriseMaterialCreate,
    EnterpriseMaterialResponse,
    EnterpriseMaterialUpdate,
    MaterialDocumentResponse,
)
from app.services.audit_service import AuditService


class MaterialService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._materials = MaterialRepository(session)
        self._documents = DocumentRepository(session)
        self._audit = AuditService(session)

    def list(self, role_codes: set[str], enterprise_id: UUID | None = None) -> list[EnterpriseMaterialResponse]:
        self._require_manager(role_codes)
        return [self._response(material) for material in self._materials.list_active(enterprise_id)]

    def delete(
        self,
        material_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
    ) -> None:
        self._require_manager(role_codes)
        material = self._get(material_id, for_update=True)
        self._materials.soft_delete(material_id)
        self._audit.record(
            actor_id=actor_id,
            action="DELETE_ENTERPRISE_MATERIAL",
            target_type="ENTERPRISE_MATERIAL",
            target_id=material.id,
            before={"name": material.name, "status": material.status},
        )
        self._session.commit()

    def create(
        self, actor_id: UUID, role_codes: set[str], payload: EnterpriseMaterialCreate
    ) -> EnterpriseMaterialResponse:
        self._require_manager(role_codes)
        self._validate_required_fields(payload.material_type, payload.model_dump())
        if payload.enterprise_id is not None and self._session.get(Enterprise, payload.enterprise_id) is None:
            raise DomainError("RESOURCE_NOT_FOUND", "归属企业不存在", 404)
        now = datetime.now(UTC)
        material_data = payload.model_dump(exclude={"self_declared"})
        material = EnterpriseMaterial(
            id=uuid4(),
            **material_data,
            status="CONFIRMED" if payload.self_declared else "PENDING",
            created_at=now,
            created_by=actor_id,
            updated_at=now,
            updated_by=actor_id,
        )
        self._materials.add(material)
        self._audit.record(
            actor_id=actor_id,
            action="CREATE_ENTERPRISE_MATERIAL",
            target_type="ENTERPRISE_MATERIAL",
            target_id=material.id,
            after={"material_type": material.material_type, "status": material.status},
        )
        self._session.commit()
        return self._response(material)

    def update(
        self,
        material_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: EnterpriseMaterialUpdate,
    ) -> EnterpriseMaterialResponse:
        self._require_manager(role_codes)
        material = self._get(material_id, for_update=True)
        before = {field: getattr(material, field) for field in payload.model_fields_set}
        for field, value in payload.model_dump(exclude_unset=True).items():
            setattr(material, field, value)
        self._validate_required_fields(material.material_type, self._material_values(material))
        if material.status == "CONFIRMED":
            self._require_confirmable(material)
        material.updated_at = datetime.now(UTC)
        material.updated_by = actor_id
        self._audit.record(
            actor_id=actor_id,
            action="UPDATE_ENTERPRISE_MATERIAL",
            target_type="ENTERPRISE_MATERIAL",
            target_id=material.id,
            before=before,
            after={"status": material.status},
        )
        self._session.commit()
        return self._response(material)

    def ensure_upload_allowed(self, material_id: UUID, role_codes: set[str]) -> None:
        self._require_manager(role_codes)
        self._get(material_id)

    def attach_document(
        self,
        material_id: UUID,
        document_id: UUID,
        document_version_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
    ) -> EnterpriseMaterialResponse:
        self._require_manager(role_codes)
        material = self._get(material_id, for_update=True)
        document = self._documents.get_document(document_id)
        version = self._documents.get_version(document_version_id)
        if (
            document is None
            or version is None
            or version.document_id != document.id
            or document.document_type != "ENTERPRISE"
            or document.deleted_at is not None
        ):
            raise DomainError("RESOURCE_NOT_FOUND", "证明文件不存在", 404)
        self._materials.add_document(
            MaterialDocument(
                material_id=material.id,
                document_id=document.id,
                document_version_id=version.id,
                relation="PROOF",
                created_at=datetime.now(UTC),
            )
        )
        self._audit.record(
            actor_id=actor_id,
            action="ATTACH_MATERIAL_DOCUMENT",
            target_type="ENTERPRISE_MATERIAL",
            target_id=material.id,
            after={"document_version_id": str(version.id)},
        )
        self._session.commit()
        return self._response(material)

    def _response(self, material: EnterpriseMaterial) -> EnterpriseMaterialResponse:
        versions = self._materials.list_document_versions(material.id)
        return EnterpriseMaterialResponse(
            id=material.id,
            enterprise_id=material.enterprise_id,
            material_type=material.material_type,
            name=material.name,
            material_no=material.material_no,
            issuer=material.issuer,
            level=material.level,
            valid_from=material.valid_from,
            valid_to=material.valid_to,
            amount=material.amount,
            currency=material.currency,
            attributes=material.attributes,
            status=material.status,
            evidence_ids=self._materials.list_evidence_ids(material.id),
            documents=[
                MaterialDocumentResponse(
                    document_id=version.document_id,
                    document_version_id=version.id,
                    file_name=version.file_name,
                    version_no=version.version_no,
                    parse_status=version.parse_status,
                )
                for version in versions
            ],
            created_at=material.created_at,
            updated_at=material.updated_at,
        )

    def _require_confirmable(self, material: EnterpriseMaterial) -> None:
        versions = self._materials.list_document_versions(material.id)
        if not any(version.parse_status == "READY" for version in versions):
            raise DomainError("EVIDENCE_REQUIRED", "确认企业材料前必须关联已解析证明文件", 409)
        if not self._materials.list_evidence_ids(material.id):
            raise DomainError("EVIDENCE_REQUIRED", "确认企业材料前必须关联 Evidence", 409)

    def _get(self, material_id: UUID, *, for_update: bool = False) -> EnterpriseMaterial:
        material = self._materials.get(material_id, for_update=for_update)
        if material is None:
            raise DomainError("RESOURCE_NOT_FOUND", "企业材料不存在", 404)
        return material

    @staticmethod
    def _require_manager(role_codes: set[str]) -> None:
        if not can_manage_enterprise_materials(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权管理企业材料", 403)

    @staticmethod
    def _material_values(material: EnterpriseMaterial) -> dict[str, object]:
        return {
            "name": material.name,
            "material_no": material.material_no,
            "valid_to": material.valid_to,
            "amount": material.amount,
            "attributes": material.attributes,
        }

    @staticmethod
    def _validate_required_fields(material_type: str, values: dict[str, object]) -> None:
        attributes = values.get("attributes")
        if not isinstance(attributes, dict):
            raise DomainError("VALIDATION_ERROR", "企业材料属性无效", 422)
        missing: list[str] = []
        if material_type in {"QUALIFICATION", "CERTIFICATE", "PERSONNEL"}:
            if not values.get("material_no"):
                missing.append("material_no")
            if values.get("valid_to") is None:
                missing.append("valid_to")
        if material_type == "PROJECT_EXPERIENCE":
            if not attributes.get("client"):
                missing.append("attributes.client")
            if not attributes.get("project_type"):
                missing.append("attributes.project_type")
            if values.get("amount") is None:
                missing.append("amount")
        if material_type == "PERSONNEL" and not attributes.get("position"):
            missing.append("attributes.position")
        if missing:
            raise DomainError(
                "VALIDATION_ERROR", f"企业材料缺少必填字段：{', '.join(missing)}", 422
            )
