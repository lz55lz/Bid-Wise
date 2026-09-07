"""报告冻结快照构建器。

只负责把数据库中的已确认事实投影为不可变的报告输入，
不负责提交、队列或 LLM。
"""

from collections import Counter
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.decisions.models import BidDecision
from app.modules.documents.models import DocumentVersion, ProjectDocument
from app.modules.evidence.models import Evidence
from app.modules.evidence.repository import EvidenceRepository
from app.modules.findings.repository import FindingRepository
from app.modules.matching.models import MaterialMatchResult
from app.modules.materials.models import EnterpriseMaterial
from app.modules.projects.models import Enterprise, ProjectEnterprise, TenderProject
from app.modules.requirements.repository import RequirementRepository
from app.modules.risks.models import ProjectRisk

_MATCH_STATUS_LABELS = {
    "MATCHED": "满足要求",
    "UNCERTAIN": "待补充核验",
    "MISSING": "存在材料缺口",
}


class ProjectReportSnapshotBuilder:
    """冻结报告需要的当前已确认事实，并保证所有可引用结论都能回溯 Evidence。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._findings = FindingRepository(session)
        self._evidences = EvidenceRepository(session)
        self._requirements = RequirementRepository(session)

    async def build(self, project_id: UUID) -> dict[str, object]:
        project = await self._session.get(TenderProject, project_id)
        if project is None:
            raise DomainError("PROJECT_NOT_FOUND", "项目不存在", 404)

        findings = await self._findings.list_in_project(project_id, "CONFIRMED")
        evidence_by_finding = await self._findings.list_evidence_ids_by_findings(
            [item.id for item in findings]
        )
        finding_rows: list[dict[str, object]] = []
        evidence_ids: list[UUID] = []
        for finding in findings:
            linked_ids = evidence_by_finding.get(finding.id, [])
            if not linked_ids:
                continue
            finding_rows.append(
                {
                    "finding_id": str(finding.id),
                    "kind": finding.kind,
                    "title": finding.title,
                    "description": finding.description[:8_000],
                    "severity": finding.severity,
                    "evidence_ids": [str(item) for item in linked_ids],
                }
            )
            evidence_ids.extend(linked_ids)

        enterprises = list(
            (
                await self._session.execute(
                    select(Enterprise, ProjectEnterprise.is_lead)
                    .join(ProjectEnterprise, ProjectEnterprise.enterprise_id == Enterprise.id)
                    .where(ProjectEnterprise.project_id == project_id)
                    .order_by(ProjectEnterprise.is_lead.desc(), Enterprise.name)
                )
            ).all()
        )
        enterprise_ids = [enterprise.id for enterprise, _ in enterprises]
        materials: list[EnterpriseMaterial] = []
        if enterprise_ids:
            materials = list(
                (
                    await self._session.scalars(
                        select(EnterpriseMaterial).where(
                            EnterpriseMaterial.enterprise_id.in_(enterprise_ids),
                            EnterpriseMaterial.status == "CONFIRMED",
                            EnterpriseMaterial.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
        material_counts = Counter(item.enterprise_id for item in materials)

        requirements = await self._requirements.list_confirmed(project_id)
        evidence_ids.extend(
            item.primary_evidence_id
            for item in requirements
            if item.primary_evidence_id is not None
        )
        unique_ids = list(dict.fromkeys(evidence_ids))
        evidences = await self._evidences.list_current_by_ids_in_project(project_id, unique_ids)
        evidence_rows = [
            {
                "evidence_id": str(item.id),
                "content": (item.quoted_text or "")[:2_000],
                "locator": item.locator,
            }
            for item in evidences
            if (item.quoted_text or "").strip()
        ]
        # 制作、盖章、密封和递交规则不是项目判断字段，也无需用户逐项录入；
        # 只在报告中作为可追溯的原文补充呈现。
        submission_notes = await self._submission_original_notes(project_id)
        evidence_rows.extend(
            item
            for item in submission_notes
            if item["evidence_id"] not in {row["evidence_id"] for row in evidence_rows}
        )
        available_ids = {item["evidence_id"] for item in evidence_rows}
        finding_rows = [
            item
            for item in finding_rows
            if any(evidence_id in available_ids for evidence_id in item["evidence_ids"])
        ]

        decision = await self._session.scalar(
            select(BidDecision).where(BidDecision.project_id == project_id)
        )
        matches = list(
            (
                await self._session.scalars(
                    select(MaterialMatchResult).where(MaterialMatchResult.project_id == project_id)
                )
            ).all()
        )
        risks = list(
            (
                await self._session.scalars(
                    select(ProjectRisk).where(ProjectRisk.project_id == project_id)
                )
            ).all()
        )
        requirements_by_id = {item.id: item for item in requirements}
        materials_by_id = {item.id: item for item in materials}
        enterprise_names = {enterprise.id: enterprise.name for enterprise, _ in enterprises}

        return {
            "project": {
                "name": project.name,
                "code": project.code,
                "purchaser": project.purchaser,
                "bid_deadline": None
                if project.bid_deadline is None
                else project.bid_deadline.isoformat(),
            },
            "enterprises": [
                {
                    "enterprise_id": str(enterprise.id),
                    "name": enterprise.name,
                    "is_lead": is_lead,
                    "confirmed_material_count": material_counts[enterprise.id],
                }
                for enterprise, is_lead in enterprises
            ],
            "qualification_requirements": [
                {
                    "requirement_id": str(item.id),
                    "title": item.title,
                    "description": item.description or "",
                    "is_mandatory": item.is_mandatory,
                    "evidence_ids": []
                    if item.primary_evidence_id is None
                    else [str(item.primary_evidence_id)],
                }
                for item in requirements
                if item.category == "QUALIFICATION"
            ],
            # 企业适配度并不只看资格项：商务、项目和评分要求同样会影响是否值得投标。
            # 保留 qualification_requirements 兼容旧报告模板，同时提供完整的匹配范围。
            "matching_requirements": [
                {
                    "requirement_id": str(item.id),
                    "title": item.title,
                    "description": item.description or "",
                    "category": item.category,
                    "is_mandatory": item.is_mandatory,
                    "evidence_ids": []
                    if item.primary_evidence_id is None
                    else [str(item.primary_evidence_id)],
                }
                for item in requirements
            ],
            "findings": finding_rows,
            "evidence": evidence_rows,
            "submission_original_notes": submission_notes,
            "decision": None
            if decision is None
            else {
                "value": decision.decision,
                "score": decision.score,
                "summary": decision.summary,
            },
            "matches": [
                {
                    "requirement_id": str(item.requirement_id),
                    "requirement_title": requirements_by_id[item.requirement_id].title
                    if item.requirement_id in requirements_by_id
                    else "已删除或未确认的需求",
                    # 报告快照面向 LLM 和最终读者；不要把数据库枚举值泄漏进正文。
                    "status": _MATCH_STATUS_LABELS.get(item.final_status, "待核验"),
                    "reason": item.reason,
                    "material_name": None
                    if item.material_id is None or item.material_id not in materials_by_id
                    else materials_by_id[item.material_id].name,
                    "enterprise_name": None
                    if item.material_id is None or item.material_id not in materials_by_id
                    else enterprise_names.get(materials_by_id[item.material_id].enterprise_id),
                    "missing_conditions": list(item.missing_conditions or []),
                }
                for item in matches
            ],
            "risks": [
                {
                    "rule_code": item.rule_code,
                    "rule_version_id": None
                    if item.rule_version_id is None
                    else str(item.rule_version_id),
                    "severity": item.severity,
                    "status": item.status,
                    "title": item.title,
                    "description": item.description,
                }
                for item in risks
            ],
        }

    async def _submission_original_notes(self, project_id: UUID) -> list[dict[str, object]]:
        """取得制作与递交事项的少量原文，不将它们误建模为待录入字段。"""
        keywords = ("投标文件格式", "签字盖章", "密封", "递交", "提交方式", "电子投标")
        rows = list(
            (
                await self._session.scalars(
                    select(Evidence)
                    .join(DocumentVersion, DocumentVersion.id == Evidence.document_version_id)
                    .join(ProjectDocument, ProjectDocument.id == DocumentVersion.document_id)
                    .where(
                        ProjectDocument.project_id == project_id,
                        ProjectDocument.deleted_at.is_(None),
                        ProjectDocument.current_version_id == DocumentVersion.id,
                        DocumentVersion.parse_status == "READY",
                        Evidence.quoted_text.is_not(None),
                        or_(*(Evidence.quoted_text.ilike(f"%{keyword}%") for keyword in keywords)),
                    )
                    .order_by(Evidence.id)
                    .limit(6)
                )
            ).all()
        )
        return [
            {
                "evidence_id": str(item.id),
                "content": (item.quoted_text or "")[:2_000],
                "locator": item.locator,
            }
            for item in rows
            if (item.quoted_text or "").strip()
        ]
