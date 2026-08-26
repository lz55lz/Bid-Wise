from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    AnalysisRun,
    AnalysisSnapshot,
    Document,
    DocumentVersion,
    EnterpriseMaterial,
    ProjectEnterprise,
    ProjectField,
    Requirement,
    RequirementEvidence,
    RuleVersion,
    TenderProject,
)


class AnalysisRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_run(self, run: AnalysisRun) -> None:
        self._session.add(run)

    def add_snapshot(self, snapshot: AnalysisSnapshot) -> None:
        self._session.add(snapshot)

    def get_run(self, run_id: UUID, *, for_update: bool = False) -> AnalysisRun | None:
        statement = select(AnalysisRun).where(AnalysisRun.id == run_id)
        if for_update:
            statement = statement.with_for_update()
        return self._session.scalar(statement)

    def list_runs(self, project_id: UUID) -> list[AnalysisRun]:
        return list(
            self._session.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.project_id == project_id)
                .order_by(AnalysisRun.created_at.desc())
            )
        )

    def get_snapshot(self, run_id: UUID) -> AnalysisSnapshot | None:
        return self._session.scalar(
            select(AnalysisSnapshot).where(AnalysisSnapshot.analysis_run_id == run_id)
        )

    def current_tender_version_ids(self, project_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(Document.current_version_id)
                .where(
                    Document.project_id == project_id,
                    Document.document_type == "TENDER",
                    Document.deleted_at.is_(None),
                    Document.current_version_id.is_not(None),
                )
                .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
                .where(DocumentVersion.parse_status == "READY")
            )
        )

    def confirmed_material_ids(self, project_id: UUID) -> list[UUID]:
        return list(
            self._session.scalars(
                select(EnterpriseMaterial.id)
                .join(
                    ProjectEnterprise,
                    ProjectEnterprise.enterprise_id == EnterpriseMaterial.enterprise_id,
                )
                .where(
                    ProjectEnterprise.project_id == project_id,
                    EnterpriseMaterial.status == "CONFIRMED",
                    EnterpriseMaterial.deleted_at.is_(None),
                )
                .order_by(EnterpriseMaterial.id)
            )
        )

    def enabled_rule_version_ids(self) -> list[UUID]:
        return list(
            self._session.scalars(
                select(RuleVersion.id)
                .where(RuleVersion.is_enabled.is_(True), RuleVersion.retired_at.is_(None))
                .order_by(RuleVersion.id)
            )
        )

    def build_input_manifest(self, project_id: UUID) -> dict[str, object]:
        """Return canonical, JSON-safe business inputs used by one analysis run."""
        project = self._session.get(TenderProject, project_id)
        tender_ids = self.current_tender_version_ids(project_id)
        versions = list(
            self._session.scalars(select(DocumentVersion).where(DocumentVersion.id.in_(tender_ids)))
        )
        requirements = list(
            self._session.scalars(
                select(Requirement)
                .where(
                    Requirement.project_id == project_id,
                    Requirement.review_status == "CONFIRMED",
                    Requirement.deleted_at.is_(None),
                )
                .order_by(Requirement.id)
            )
        )
        requirement_ids = [item.id for item in requirements]
        evidence_map: dict[UUID, list[str]] = {item_id: [] for item_id in requirement_ids}
        if requirement_ids:
            for requirement_id, evidence_id in self._session.execute(
                select(RequirementEvidence.requirement_id, RequirementEvidence.evidence_id)
                .where(RequirementEvidence.requirement_id.in_(requirement_ids))
                .order_by(RequirementEvidence.requirement_id, RequirementEvidence.evidence_id)
            ).tuples():
                evidence_map[requirement_id].append(str(evidence_id))
        material_ids = self.confirmed_material_ids(project_id)
        materials = list(
            self._session.scalars(
                select(EnterpriseMaterial).where(EnterpriseMaterial.id.in_(material_ids))
            )
        )
        rules = list(
            self._session.scalars(
                select(RuleVersion)
                .where(RuleVersion.id.in_(self.enabled_rule_version_ids()))
                .order_by(RuleVersion.id)
            )
        )
        project_fields = list(
            self._session.scalars(
                select(ProjectField)
                .where(
                    ProjectField.project_id == project_id,
                    ProjectField.review_status == "CONFIRMED",
                )
                .order_by(ProjectField.field_code, ProjectField.id)
            )
        )
        return {
            "project_facts": {
                "manual_bid_deadline": (
                    project.bid_deadline.isoformat()
                    if project is not None and project.bid_deadline is not None
                    else None
                ),
                "confirmed_fields": [
                    {
                        "id": str(item.id),
                        "field_code": item.field_code,
                        "value_json": item.value_json,
                        "confidence": str(item.confidence) if item.confidence is not None else None,
                        "primary_evidence_id": (
                            str(item.primary_evidence_id)
                            if item.primary_evidence_id is not None
                            else None
                        ),
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in project_fields
                ],
            },
            "tender_versions": [
                {
                    "id": str(item.id),
                    "sha256": item.sha256,
                    # Nodes were labelled before a project-level analysis
                    # starts. Including this immutable policy fingerprint in
                    # the manifest makes a historical report reproducible
                    # even after the tag dictionary evolves.
                    "node_label_policy": (item.cleaning_summary or {}).get(
                        "node_label_policy"
                    ),
                }
                for item in versions
            ],
            "requirements": [
                {
                    "id": str(item.id),
                    "category": item.category,
                    "title": item.title,
                    "description": item.description,
                    "conditions": item.conditions,
                    "is_mandatory": item.is_mandatory,
                    "score": str(item.score) if item.score is not None else None,
                    "evidence_ids": evidence_map[item.id],
                }
                for item in requirements
            ],
            "materials": [
                {
                    "id": str(item.id),
                    "enterprise_id": str(item.enterprise_id) if item.enterprise_id else None,
                    "material_type": item.material_type,
                    "name": item.name,
                    "material_no": item.material_no,
                    "valid_to": item.valid_to.isoformat() if item.valid_to else None,
                    "amount": str(item.amount) if item.amount is not None else None,
                    "attributes": item.attributes,
                }
                for item in materials
            ],
            "rules": [
                {"id": str(item.id), "definition": item.definition, "severity": item.severity}
                for item in rules
            ],
        }
