"""Batch query layer between persisted facts and report rendering.

Report sections consume this context instead of each deciding independently
which ProjectField spelling or review status is usable.  The context is kept
in-memory for one report generation; PostgreSQL remains the source of truth.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import MatchResult, ProjectField, Requirement, Risk
from app.db.repositories.match_repository import MatchRepository
from app.db.repositories.project_field_repository import ProjectFieldRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.db.repositories.risk_repository import RiskRepository
from app.services.material_match_policy import is_enterprise_material_requirement
from app.services.project_field_registry import canonical_project_field_code

_SCHEDULE_HARD_KEYWORDS = (
    "投标截止", "递交截止", "开标时间", "开标地点", "投标保证金", "答疑",
)
_SUBMISSION_DOCUMENT_KEYWORDS = ("投标文件", "响应文件", "电子投标文件")
_SUBMISSION_OPERATION_KEYWORDS = ("递交", "签章", "签名", "密封", "加密", "解密", "上传")
_RISK_SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


@dataclass(frozen=True, slots=True)
class ActionPlanItem:
    priority: str
    action: str
    evidence_ids: list[UUID]


@dataclass(frozen=True, slots=True)
class ReportFieldQueryContext:
    """Confirmed facts and analysis results loaded once for one report."""

    project_fields: dict[str, ProjectField]
    confirmed_requirements: list[Requirement]
    all_requirements: list[Requirement]
    risks: list[Risk]
    matches: list[MatchResult]
    requirement_evidence: dict[UUID, list[UUID]]
    risk_evidence: dict[UUID, list[UUID]]
    match_evidence: dict[UUID, list[tuple[UUID, str]]]

    def value_for(self, field_code: str) -> str | None:
        """Return a display-safe scalar without inventing a missing value."""
        field = self.project_fields.get(canonical_project_field_code(field_code))
        if field is None:
            return None
        value = (field.value_json or {}).get("value")
        if value is None or value == "":
            return None
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ",:"))
        return str(value)

    def evidence_for(self, field_code: str) -> list[UUID]:
        field = self.project_fields.get(canonical_project_field_code(field_code))
        if field is None or field.primary_evidence_id is None:
            return []
        return [field.primary_evidence_id]

    def schedule_requirements(self) -> list[Requirement]:
        """Return confirmed bidder-facing schedule and submission requirements."""
        matched = []
        for requirement in self.confirmed_requirements:
            if requirement.category != "BUSINESS":
                continue
            content = f"{requirement.title} {requirement.description or ''}"
            has_hard_schedule_signal = any(
                keyword in content for keyword in _SCHEDULE_HARD_KEYWORDS
            )
            has_submission_operation = (
                any(keyword in content for keyword in _SUBMISSION_DOCUMENT_KEYWORDS)
                and any(keyword in content for keyword in _SUBMISSION_OPERATION_KEYWORDS)
            )
            if has_hard_schedule_signal or has_submission_operation:
                matched.append(requirement)
        return sorted(
            matched,
            key=lambda requirement: (
                0 if requirement.is_mandatory else 1,
                requirement.created_at,
                str(requirement.id),
            ),
        )

    def scoring_requirements(self) -> list[Requirement]:
        """Return only confirmed clauses with an actual quantitative score."""
        return sorted(
            (
                requirement
                for requirement in self.confirmed_requirements
                if requirement.category == "SCORING" and requirement.score is not None
            ),
            key=lambda requirement: (
                requirement.score is None,
                -(float(requirement.score) if requirement.score is not None else 0),
                0 if requirement.is_mandatory else 1,
                requirement.created_at,
            ),
        )

    def open_risks(self) -> list[Risk]:
        """Return only actionable current risks with one shared severity ordering."""
        return sorted(
            (
                risk
                for risk in self.risks
                if risk.status in {"PENDING", "CONFIRMED"}
            ),
            key=lambda risk: (
                _RISK_SEVERITY_RANK.get(risk.severity, 99),
                risk.created_at,
                str(risk.id),
            ),
        )

    def action_plan_items(self) -> list[ActionPlanItem]:
        """Build deterministic actions from open risks and enterprise-match gaps."""
        items: list[ActionPlanItem] = []
        seen_risk_rules: set[UUID | None] = set()
        covered_requirement_ids: set[UUID] = set()
        for risk in self.open_risks():
            if risk.severity not in {"CRITICAL", "HIGH"}:
                continue
            if risk.rule_version_id in seen_risk_rules:
                continue
            seen_risk_rules.add(risk.rule_version_id)
            requirement_title = (risk.trigger_data or {}).get("requirement_title")
            requirement_id = (risk.trigger_data or {}).get("requirement_id")
            if isinstance(requirement_id, str):
                try:
                    covered_requirement_ids.add(UUID(requirement_id))
                except ValueError:
                    pass
            if isinstance(requirement_title, str) and requirement_title:
                action = f"补齐“{requirement_title}”所需的企业证明材料"
            else:
                action = f"处理 {risk.severity} 风险：{risk.title}"
            items.append(
                ActionPlanItem(
                    priority="P0" if risk.severity == "CRITICAL" else "P1",
                    action=action,
                    evidence_ids=self.risk_evidence.get(risk.id, []),
                )
            )
            if len(items) >= 5:
                return items

        gaps = self.enterprise_gaps()
        if gaps and any(match.requirement_id not in covered_requirement_ids for match in gaps):
            evidence_ids: list[UUID] = []
            for match in gaps:
                evidence_ids.extend(
                    evidence_id for evidence_id, _ in self.match_evidence.get(match.id, [])
                )
                evidence_ids.extend(self.requirement_evidence.get(match.requirement_id, []))
            missing_count = sum(match.final_status == "MISSING" for match in gaps)
            items.append(
                ActionPlanItem(
                    priority="P0" if missing_count else "P1",
                    action=(
                        f"补齐并确认 {len(gaps)} 项企业材料缺口"
                        "（具体要求见“资格条件与企业符合情况”）"
                    ),
                    evidence_ids=list(dict.fromkeys(evidence_ids)),
                )
            )
        return items

    def enterprise_gaps(self) -> list[MatchResult]:
        """Only expose real enterprise-proof gaps, once per Requirement."""
        requirements = {item.id: item for item in self.all_requirements}
        rank = {"MISSING": 0, "UNCERTAIN": 1}
        selected: dict[UUID, MatchResult] = {}
        for match in self.matches:
            requirement = requirements.get(match.requirement_id)
            if (
                match.final_status not in rank
                or not is_enterprise_material_requirement(requirement)
            ):
                continue
            current = selected.get(match.requirement_id)
            if current is None or rank[match.final_status] < rank[current.final_status]:
                selected[match.requirement_id] = match
        return sorted(
            selected.values(),
            key=lambda match: (rank[match.final_status], str(match.requirement_id)),
        )


class ReportFieldQueryService:
    """Load the registered report inputs in bounded, batch repository calls."""

    def __init__(self, session: Session) -> None:
        self._project_fields = ProjectFieldRepository(session)
        self._requirements = RequirementRepository(session)
        self._risks = RiskRepository(session)
        self._matches = MatchRepository(session)

    def load(self, project_id: UUID) -> ReportFieldQueryContext:
        fields = self._index_confirmed_fields(
            self._project_fields.list_for_project(project_id)
        )
        confirmed_requirements = self._requirements.list_confirmed_for_project(project_id)
        all_requirements = self._requirements.list_for_project(project_id)
        risks = self._risks.list_current_for_project(project_id)
        matches = self._matches.list_current_for_project(project_id)

        requirement_ids = [item.id for item in confirmed_requirements]
        risk_ids = [item.id for item in risks]
        match_ids = [item.id for item in matches]
        return ReportFieldQueryContext(
            project_fields=fields,
            confirmed_requirements=confirmed_requirements,
            all_requirements=all_requirements,
            risks=risks,
            matches=matches,
            requirement_evidence=(
                self._requirements.list_evidence_ids_for_requirements(requirement_ids)
                if requirement_ids
                else {}
            ),
            risk_evidence=(
                self._risks.list_evidence_ids_for_risks(risk_ids) if risk_ids else {}
            ),
            match_evidence=(
                self._matches.list_evidence_links_for_matches(match_ids)
                if match_ids
                else {}
            ),
        )

    @staticmethod
    def _index_confirmed_fields(
        fields: list[ProjectField],
    ) -> dict[str, ProjectField]:
        """Choose one reviewed field per canonical code, preferring current spelling."""
        indexed: dict[str, ProjectField] = {}
        for field in fields:
            if field.review_status != "CONFIRMED" or field.primary_evidence_id is None:
                continue
            code = canonical_project_field_code(field.field_code)
            current = indexed.get(code)
            if current is None or ReportFieldQueryService._field_rank(field, code) > (
                ReportFieldQueryService._field_rank(current, code)
            ):
                indexed[code] = field
        return indexed

    @staticmethod
    def _field_rank(field: ProjectField, canonical_code: str) -> tuple[bool, Any, Any, str]:
        return (
            field.field_code == canonical_code,
            field.confidence or 0,
            field.updated_at,
            str(field.id),
        )
