from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple, Protocol
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.constants import LEGAL_COMPLIANCE
from app.core.errors import DomainError
from app.core.permissions import can_write_project_documents
from app.db.models import (
    Evidence,
    Risk,
    RiskEvidence,
    RiskReview,
    Rule,
    RuleVersion,
    TenderProject,
)
from app.db.repositories.match_repository import MatchRepository
from app.db.repositories.material_repository import MaterialRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.db.repositories.risk_repository import RiskRepository, RuleRepository
from app.schemas.documents import TaskResponse
from app.schemas.risks import RiskResponse, RiskReviewRequest
from app.services.audit_service import AuditService
from app.services.material_match_policy import is_enterprise_material_requirement
from app.services.project_fact_resolver import ProjectFactResolver
from app.services.project_service import ProjectService
from app.services.task_service import RUN_RISK_CHECK, TaskService

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

_CERTIFICATE_MATERIAL_TYPES = frozenset({"QUALIFICATION", "CERTIFICATE"})
_QUANTITATIVE_MATERIAL_TYPES = frozenset(
    {"QUALIFICATION", "CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"}
)
_MATCHED_STATUSES = frozenset({"MATCHED", "PARTIAL"})

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "PENDING": frozenset({"PENDING", "CONFIRMED", "RESOLVED", "FALSE_POSITIVE", "IGNORED"}),
    "CONFIRMED": frozenset({"CONFIRMED", "RESOLVED", "FALSE_POSITIVE", "IGNORED"}),
    "RESOLVED": frozenset({"RESOLVED", "PENDING"}),
    "FALSE_POSITIVE": frozenset({"FALSE_POSITIVE", "PENDING"}),
    "IGNORED": frozenset({"IGNORED", "PENDING"}),
}


class _BuiltinRuleSpec(NamedTuple):
    code: str
    name: str
    risk_type: str
    severity: str
    definition: dict[str, Any]


_BUILTIN_RULES: dict[str, _BuiltinRuleSpec] = {
    "DEADLINE_EXPIRED": _BuiltinRuleSpec(
        code="DEADLINE_EXPIRED",
        name="投标截止时间已过",
        risk_type="TIME",
        severity="CRITICAL",
        definition={
            "all": [
                {"source": "project", "field": "bid_deadline", "op": "LT_NOW"},
                {"source": "project", "field": "bid_deadline", "op": "EXISTS"},
            ],
            "message_template": "投标截止时间已过",
            "evidence_selector": {"field_code": "bid_deadline"},
        },
    ),
    "CERTIFICATE_EXPIRED": _BuiltinRuleSpec(
        code="CERTIFICATE_EXPIRED",
        name="企业证书已过期",
        risk_type="QUALIFICATION",
        severity="HIGH",
        definition={
            "all": [
                {
                    "source": "material",
                    "field": "valid_to",
                    "op": "DATE_BEFORE",
                    "value": {"source": "project", "field": "bid_deadline"},
                }
            ],
            "message_template": "证书在投标截止日前失效",
            "evidence_selector": {"source": "material", "field_code": "valid_to"},
        },
    ),
    "QUANTITATIVE_REQUIREMENT_UNMET": _BuiltinRuleSpec(
        code="QUANTITATIVE_REQUIREMENT_UNMET",
        name="定量资格条件未满足",
        risk_type="QUALIFICATION",
        severity="HIGH",
        definition={
            "all": [{"source": "requirement", "field": "conditions", "op": "EXISTS"}],
            "message_template": "定量资格条件未满足",
            "evidence_selector": {"source": "requirement", "field_code": "conditions"},
        },
    ),
    "MANDATORY_EVIDENCE_MISSING": _BuiltinRuleSpec(
        code="MANDATORY_EVIDENCE_MISSING",
        name="强制 Requirement 缺少材料证据",
        risk_type="DOCUMENT",
        severity="HIGH",
        definition={
            "all": [
                {
                    "source": "requirement",
                    "field": "is_mandatory",
                    "op": "EQ",
                    "value": True,
                }
            ],
            "message_template": "强制 Requirement 缺少材料证据",
            "evidence_selector": {"source": "requirement", "field_code": "is_mandatory"},
        },
    ),
}


# ---------------------------------------------------------------------------
# Internal data structures
# ---------------------------------------------------------------------------


@dataclass
class _CheckContext:
    """Pre-loaded data for a single risk-check run, avoiding N+1 queries."""

    project_id: UUID
    bid_deadline: datetime | None
    deadline: date | None
    deadline_precision: str
    deadline_evidence_ids: list[UUID]
    requirements: list[Any]
    materials: list[Any]
    requirement_evidence: dict[UUID, list[UUID]]
    material_evidence: dict[UUID, list[UUID]]
    matched_satisfied: set[UUID]
    has_matches: bool
    now: datetime


class _Publisher(Protocol):
    def publish_run_risk_check(self, task_id: UUID, project_id: UUID) -> str: ...


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RiskService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = ProjectService(session)
        self._requirements = RequirementRepository(session)
        self._materials = MaterialRepository(session)
        self._rules = RuleRepository(session)
        self._risks = RiskRepository(session)
        self._matches = MatchRepository(session)
        self._audit = AuditService(session)
        self._project_facts = ProjectFactResolver(session)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[RiskResponse]:
        now = datetime.now(UTC)
        project = self._authorize_for_run(project_id, actor_id, role_codes)
        enterprise_ids = self._require_bound_enterprise_ids(project)
        versions = self._ensure_builtin_versions(actor_id, now)
        ctx = self._build_context(project, enterprise_ids, now)

        self._risks.mark_not_current_for_project(project.id)
        self._run_builtin_checks(ctx, versions, actor_id, now)
        self._run_custom_rules(project, actor_id, now)

        self._audit.record(
            actor_id=actor_id,
            action="RUN_RISK_CHECK",
            target_type="PROJECT",
            target_id=project.id,
            project_id=project.id,
        )
        self._session.commit()
        return [
            self._response(risk)
            for risk in self._risks.list_current_for_project(project.id)
        ]

    def list(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> list[RiskResponse]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        return [
            self._response(risk)
            for risk in self._risks.list_current_for_project(project_id)
        ]

    def submit(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        publisher: _Publisher,
    ) -> TaskResponse:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        self._require_reviewer(role_codes)
        enterprise_ids = self._require_bound_enterprise_ids(project)
        deadline = self._project_facts.resolve_bid_deadline(project)
        task = TaskService(self._session).create_risk_check_task(
            project_id,
            actor_id,
            self._state_hash(
                project_id,
                deadline.fingerprint(),
                enterprise_ids,
            ),
        )
        self._session.commit()
        if task.status == "QUEUED" and task.celery_task_id is None:
            try:
                task.celery_task_id = publisher.publish_run_risk_check(task.id, project_id)
                self._session.commit()
            except Exception:
                self._session.rollback()
                TaskService(self._session).fail_project_task(
                    task.id,
                    RUN_RISK_CHECK,
                    "TASK_QUEUE_UNAVAILABLE",
                    "风险检查任务队列暂不可用，请稍后重试。",
                )
        return self._task_response(task)

    def review(
        self,
        project_id: UUID,
        risk_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: RiskReviewRequest,
    ) -> RiskResponse:
        risk = self._risks.get(risk_id, for_update=True)
        if risk is None or risk.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "风险不存在", 404)
        project = self._projects.get_visible(risk.project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        self._require_reviewer(role_codes)
        self._validate_transition(risk.status, payload.status)
        resolution = (payload.resolution or "").strip()
        if payload.status != "PENDING" and not resolution:
            raise DomainError(
                "VALIDATION_ERROR", "变更风险状态时必须填写处理说明", 422
            )
        now = datetime.now(UTC)
        previous_status = risk.status
        risk.status = payload.status
        risk.resolution = resolution or None
        risk.updated_at = now
        review_resolution = resolution or "恢复待复核"
        self._session.add(
            RiskReview(
                id=uuid4(),
                risk_id=risk.id,
                from_status=previous_status,
                to_status=risk.status,
                resolution=review_resolution,
                reviewed_by=actor_id,
                reviewed_at=now,
            )
        )
        confirmation = Evidence(
            id=uuid4(),
            source_type="USER_CONFIRMATION",
            document_version_id=None,
            document_node_id=None,
            page_number=None,
            quoted_text=review_resolution,
            content_hash=hashlib.sha256(review_resolution.encode("utf-8")).hexdigest(),
            bbox=None,
            source_reference={"risk_id": str(risk.id), "status": risk.status},
            created_at=now,
            created_by=actor_id,
        )
        self._session.add(confirmation)
        self._add_evidence_links(risk.id, [confirmation.id], now)
        self._audit.record(
            actor_id=actor_id,
            action="REVIEW_RISK",
            target_type="RISK",
            target_id=risk.id,
            project_id=risk.project_id,
            before={"status": previous_status},
            after={"status": risk.status},
        )
        self._session.commit()
        return self._response(risk)

    # ------------------------------------------------------------------
    # Authorization
    # ------------------------------------------------------------------

    def _authorize_for_run(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> TenderProject:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        self._require_reviewer(role_codes)
        return project

    # ------------------------------------------------------------------
    # Context building
    # ------------------------------------------------------------------

    def _build_context(
        self,
        project: TenderProject,
        enterprise_ids: list[UUID],
        now: datetime,
    ) -> _CheckContext:
        bid_deadline = self._project_facts.resolve_bid_deadline(project)
        requirements = self._requirements.list_confirmed_for_project(project.id)
        materials = self._materials.list_confirmed_for_enterprises(enterprise_ids)

        req_ids = [r.id for r in requirements]
        mat_ids = [m.id for m in materials]

        requirement_evidence = (
            self._requirements.list_evidence_ids_for_requirements(req_ids)
            if req_ids
            else {}
        )
        material_evidence = (
            self._materials.list_evidence_ids_for_materials(mat_ids)
            if mat_ids
            else {}
        )

        current_matches = self._matches.list_current_for_project(project.id)
        matched_satisfied = {
            m.requirement_id
            for m in current_matches
            if m.final_status in _MATCHED_STATUSES
        }

        return _CheckContext(
            project_id=project.id,
            bid_deadline=bid_deadline.value,
            deadline=bid_deadline.date,
            deadline_precision=bid_deadline.precision,
            deadline_evidence_ids=bid_deadline.evidence_ids,
            requirements=requirements,
            materials=materials,
            requirement_evidence=requirement_evidence,
            material_evidence=material_evidence,
            matched_satisfied=matched_satisfied,
            has_matches=bool(current_matches),
            now=now,
        )

    # ------------------------------------------------------------------
    # Builtin rule checks
    # ------------------------------------------------------------------

    def _run_builtin_checks(
        self,
        ctx: _CheckContext,
        versions: dict[str, RuleVersion],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        self._check_deadline_expired(ctx, versions, actor_id, now)
        self._check_certificate_expired(ctx, versions, actor_id, now)
        self._check_quantitative_unmet(ctx, versions, actor_id, now)
        self._check_mandatory_missing(ctx, versions, actor_id, now)

    def _check_deadline_expired(
        self,
        ctx: _CheckContext,
        versions: dict[str, RuleVersion],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        version = versions.get("DEADLINE_EXPIRED")
        if version is None or ctx.bid_deadline is None:
            return
        if ctx.deadline_precision == "DATE":
            expired = ctx.deadline is not None and ctx.deadline < now.date()
        else:
            expired = ctx.bid_deadline < now
        if not expired:
            return
        spec = _BUILTIN_RULES["DEADLINE_EXPIRED"]
        self._upsert(
            project_id=ctx.project_id,
            version=version,
            risk_type=spec.risk_type,
            subject="project-deadline",
            title="投标截止时间已过",
            description="投标截止时间已早于当前时间，项目建议不得继续推进。",
            trigger_data={
                "subject": "project-deadline",
                "bid_deadline": ctx.bid_deadline.isoformat(),
                "precision": ctx.deadline_precision,
            },
            source_evidence_ids=ctx.deadline_evidence_ids,
            actor_id=actor_id,
            now=now,
        )

    def _check_certificate_expired(
        self,
        ctx: _CheckContext,
        versions: dict[str, RuleVersion],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        version = versions.get("CERTIFICATE_EXPIRED")
        if version is None:
            return
        spec = _BUILTIN_RULES["CERTIFICATE_EXPIRED"]
        for material in ctx.materials:
            if material.material_type not in _CERTIFICATE_MATERIAL_TYPES:
                continue
            if (
                ctx.deadline is None
                or material.valid_to is None
                or material.valid_to >= ctx.deadline
            ):
                continue
            self._upsert(
                project_id=ctx.project_id,
                version=version,
                risk_type=spec.risk_type,
                subject=f"material:{material.id}",
                title=f"企业资质或证书已过期:{material.name}",
                description=(
                    f"「{material.name}」有效期至 {material.valid_to.isoformat()},"
                    f"早于本项目投标截止日 {ctx.deadline.isoformat()}。"
                ),
                trigger_data={
                    "subject": f"material:{material.id}",
                    "material_id": str(material.id),
                    "material_name": material.name,
                    "valid_to": material.valid_to.isoformat(),
                },
                source_evidence_ids=ctx.material_evidence.get(material.id, []),
                actor_id=actor_id,
                now=now,
            )

    def _check_quantitative_unmet(
        self,
        ctx: _CheckContext,
        versions: dict[str, RuleVersion],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        version = versions.get("QUANTITATIVE_REQUIREMENT_UNMET")
        if version is None:
            return
        spec = _BUILTIN_RULES["QUANTITATIVE_REQUIREMENT_UNMET"]

        # Hoist candidate filtering out of the inner loop — it does not depend
        # on the requirement or dimension being evaluated.
        candidates = [
            m for m in ctx.materials if m.material_type in _QUANTITATIVE_MATERIAL_TYPES
        ]

        for requirement in ctx.requirements:
            if requirement.category != "QUALIFICATION":
                continue
            req_evidence = ctx.requirement_evidence.get(requirement.id, [])
            for dimension, expected in self._quantitative_conditions(
                requirement.conditions
            ):
                actual = self._max_numeric(candidates, dimension)
                if actual is not None and actual >= expected:
                    continue
                dimension_label = "数量" if dimension == "count" else "金额"
                actual_label = "无可用材料" if actual is None else str(actual)
                self._upsert(
                    project_id=ctx.project_id,
                    version=version,
                    risk_type=spec.risk_type,
                    subject=f"requirement:{requirement.id}:{dimension}",
                    title=f"定量资格条件未满足:{requirement.title}",
                    description=(
                        f"「{requirement.title}」要求{dimension_label}不低于 {expected},"
                        f"已确认企业材料最高为 {actual_label}。"
                    ),
                    trigger_data={
                        "subject": f"requirement:{requirement.id}:{dimension}",
                        "requirement_id": str(requirement.id),
                        "requirement_title": requirement.title,
                        "dimension": dimension,
                        "required": str(expected),
                        "actual": None if actual is None else str(actual),
                    },
                    source_evidence_ids=req_evidence,
                    actor_id=actor_id,
                    now=now,
                )

    def _check_mandatory_missing(
        self,
        ctx: _CheckContext,
        versions: dict[str, RuleVersion],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        version = versions.get("MANDATORY_EVIDENCE_MISSING")
        if version is None:
            return

        # Skip only when the project has no materials, no match results,
        # and no evaluable DSL conditions — in that case the rule would be
        # pure noise since satisfaction can only be assessed manually.
        skip = (
            not ctx.materials
            and not ctx.has_matches
            and not self._has_evaluable_requirements(ctx.requirements)
        )
        if skip:
            return

        spec = _BUILTIN_RULES["MANDATORY_EVIDENCE_MISSING"]
        for requirement in ctx.requirements:
            # Matching deliberately skips project facts and bidding-conduct
            # constraints.  They cannot be truthfully labelled as a missing
            # enterprise material merely because no MatchResult exists.
            if not requirement.is_mandatory or not is_enterprise_material_requirement(
                requirement
            ):
                continue
            if requirement.id in ctx.matched_satisfied:
                continue
            self._upsert(
                project_id=ctx.project_id,
                version=version,
                risk_type=spec.risk_type,
                subject=f"requirement:{requirement.id}",
                title=f"强制 Requirement 缺少材料证据:{requirement.title}",
                description=(
                    f"「{requirement.title}」为强制要求，但没有可关联的 MatchResult"
                    "(MATCHED/PARTIAL),不能确认该强制 Requirement 已满足。"
                ),
                trigger_data={
                    "subject": f"requirement:{requirement.id}",
                    "requirement_id": str(requirement.id),
                    "requirement_title": requirement.title,
                },
                source_evidence_ids=ctx.requirement_evidence.get(requirement.id, []),
                actor_id=actor_id,
                now=now,
            )

    # ------------------------------------------------------------------
    # Custom (DB-defined) rule checks
    # ------------------------------------------------------------------

    def _run_custom_rules(
        self,
        project: TenderProject,
        actor_id: UUID,
        now: datetime,
    ) -> None:
        for rule, version in self._rules.list_active_versions():
            if rule.code in _BUILTIN_RULES:
                continue
            if not self._matches_project_rule(version.definition, project, now):
                continue
            self._upsert(
                project_id=project.id,
                version=version,
                risk_type=rule.risk_type,
                subject="project",
                title=rule.name,
                description=str(version.definition["message_template"]),
                trigger_data={"subject": "project", "rule_code": rule.code},
                source_evidence_ids=[],
                actor_id=actor_id,
                now=now,
            )

    # ------------------------------------------------------------------
    # Risk upsert
    # ------------------------------------------------------------------

    def _upsert(
        self,
        *,
        project_id: UUID,
        version: RuleVersion,
        risk_type: str,
        subject: str,
        title: str,
        description: str,
        trigger_data: dict[str, object],
        source_evidence_ids: list[UUID],
        actor_id: UUID,
        now: datetime,
    ) -> None:
        risk = self._risks.find_by_rule_subject(project_id, version.id, subject)
        if risk is None:
            system_evidence = self._system_rule_evidence(
                version.id, project_id, subject, actor_id, now
            )
            evidence_ids = [*source_evidence_ids, system_evidence.id]
            risk = Risk(
                id=uuid4(),
                project_id=project_id,
                rule_version_id=version.id,
                risk_type=risk_type,
                severity=version.severity,
                title=title,
                description=description,
                trigger_data=trigger_data,
                confidence=None,
                status="PENDING",
                resolution=None,
                primary_evidence_id=evidence_ids[0],
                is_current=True,
                created_at=now,
                updated_at=now,
            )
            self._risks.add(risk)
            self._session.flush()
        else:
            evidence_ids = source_evidence_ids
            risk.severity = version.severity
            risk.title = title
            risk.description = description
            risk.trigger_data = trigger_data
            if evidence_ids:
                risk.primary_evidence_id = evidence_ids[0]
            risk.is_current = True
            risk.updated_at = now
        self._add_evidence_links(risk.id, evidence_ids, now)

    # ------------------------------------------------------------------
    # Evidence helpers
    # ------------------------------------------------------------------

    def _system_rule_evidence(
        self,
        rule_version_id: UUID,
        project_id: UUID,
        subject: str,
        actor_id: UUID,
        now: datetime,
    ) -> Evidence:
        reference = {
            "rule_version_id": str(rule_version_id),
            "project_id": str(project_id),
            "subject": subject,
        }
        serialized = f"{rule_version_id}:{project_id}:{subject}"
        evidence = Evidence(
            id=uuid4(),
            source_type="SYSTEM_RULE",
            document_version_id=None,
            document_node_id=None,
            page_number=None,
            quoted_text="系统规则命中",
            content_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
            bbox=None,
            source_reference=reference,
            created_at=now,
            created_by=actor_id,
        )
        self._session.add(evidence)
        return evidence

    def _add_evidence_links(
        self, risk_id: UUID, evidence_ids: list[UUID], now: datetime
    ) -> None:
        existing = set(self._risks.list_evidence_ids(risk_id))
        for evidence_id in evidence_ids:
            if evidence_id not in existing:
                self._risks.add_evidence(
                    RiskEvidence(
                        risk_id=risk_id, evidence_id=evidence_id, created_at=now
                    )
                )

    # ------------------------------------------------------------------
    # Builtin rule version bootstrap
    # ------------------------------------------------------------------

    def _ensure_builtin_versions(
        self, actor_id: UUID, now: datetime
    ) -> dict[str, RuleVersion]:
        versions: dict[str, RuleVersion] = {}
        for spec in _BUILTIN_RULES.values():
            rule = self._rules.get_by_code(spec.code)
            if rule is None:
                rule = Rule(
                    id=uuid4(),
                    code=spec.code,
                    name=spec.name,
                    risk_type=spec.risk_type,
                    created_at=now,
                    created_by=actor_id,
                )
                self._rules.add_rule(rule)
                self._session.flush()
                version = RuleVersion(
                    id=uuid4(),
                    rule_id=rule.id,
                    version_no=1,
                    severity=spec.severity,
                    definition=spec.definition,
                    is_enabled=True,
                    effective_at=now,
                    retired_at=None,
                    created_at=now,
                    created_by=actor_id,
                )
                self._rules.add_version(version)
                versions[spec.code] = version
                continue
            version = self._rules.get_active_version(rule.id)
            if version is not None:
                versions[spec.code] = version
        return versions

    # ------------------------------------------------------------------
    # Rule condition evaluation
    # ------------------------------------------------------------------

    @staticmethod
    def _has_evaluable_requirements(requirements: list[Any]) -> bool:
        """Return True if any requirement has a non-empty DSL ``all`` condition.

        When all conditions are empty (``{}`` or ``{"all": []}``), the match
        service produces no results and the mandatory-evidence-missing rule
        would be pure noise.
        """
        for requirement in requirements:
            value = requirement.conditions
            if (
                isinstance(value, dict)
                and set(value) == {"all"}
                and isinstance(value["all"], list)
                and value["all"]
            ):
                return True
        return False

    @staticmethod
    def _quantitative_conditions(value: object) -> list[tuple[str, Decimal]]:
        if not isinstance(value, dict) or not isinstance(value.get("all"), list):
            return []
        conditions: list[tuple[str, Decimal]] = []
        for item in value["all"]:
            if not isinstance(item, dict):
                continue
            dimension = item.get("dimension")
            if dimension not in {"count", "amount"} or item.get("operator") != "GTE":
                continue
            try:
                expected = Decimal(str(item.get("value")))
            except (InvalidOperation, ValueError):
                continue
            if expected >= 0:
                conditions.append((dimension, expected))
        return conditions

    @staticmethod
    def _matches_project_rule(
        definition: object, project: TenderProject, now: datetime
    ) -> bool:
        if not isinstance(definition, dict) or not isinstance(
            definition.get("all"), list
        ):
            return False
        for condition in definition["all"]:
            if not isinstance(condition, dict) or condition.get("source") != "project":
                return False
            field = condition.get("field")
            operator = condition.get("op")
            if not isinstance(field, str) or not isinstance(operator, str):
                return False
            actual = getattr(project, field, None)
            expected = condition.get("value")
            if isinstance(expected, dict):
                if expected.get("source") != "project" or not isinstance(
                    expected.get("field"), str
                ):
                    return False
                expected = getattr(project, expected["field"], None)
            if not RiskService._rule_condition_matches(
                actual, operator, expected, now
            ):
                return False
        return True

    @staticmethod
    def _rule_condition_matches(
        actual: object, operator: str, expected: object, now: datetime
    ) -> bool:
        if operator == "EXISTS":
            return actual is not None
        if operator == "NOT_EXISTS":
            return actual is None
        if operator == "LT_NOW":
            return isinstance(actual, datetime) and actual < now
        if operator == "IN":
            return isinstance(expected, list) and actual in expected
        if operator in {"EQ", "NE"}:
            return (actual == expected) if operator == "EQ" else (actual != expected)
        if operator in {"GT", "GTE", "LT", "LTE", "DATE_BEFORE"}:
            try:
                if isinstance(actual, datetime) and isinstance(expected, datetime):
                    left, right = actual, expected
                else:
                    left, right = Decimal(str(actual)), Decimal(str(expected))
            except (InvalidOperation, TypeError, ValueError):
                return False
            if operator == "GT":
                return left > right
            if operator == "GTE":
                return left >= right
            if operator in {"LT", "DATE_BEFORE"}:
                return left < right
            return left <= right
        return False

    @staticmethod
    def _max_numeric(materials: list[Any], dimension: str) -> Decimal | None:
        values: list[Decimal] = []
        for material in materials:
            raw_value = (
                material.amount
                if dimension == "amount"
                else material.attributes.get("count")
            )
            if raw_value is None:
                continue
            try:
                values.append(Decimal(str(raw_value)))
            except (InvalidOperation, ValueError):
                continue
        return max(values) if values else None

    @staticmethod
    def _compatible_material_types(category: str) -> set[str]:
        if category == "QUALIFICATION":
            return {"QUALIFICATION", "CERTIFICATE", "PERSONNEL"}
        if category == "BUSINESS":
            return {"PROJECT_EXPERIENCE"}
        return {"QUALIFICATION", "CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"}

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_reviewer(role_codes: set[str]) -> None:
        if LEGAL_COMPLIANCE not in role_codes and not can_write_project_documents(
            role_codes
        ):
            raise DomainError("PERMISSION_DENIED", "无权复核风险", 403)

    @staticmethod
    def _validate_transition(previous: str, target: str) -> None:
        if target not in _ALLOWED_TRANSITIONS.get(previous, frozenset()):
            raise DomainError("INVALID_STATE_TRANSITION", "风险状态转换不合法", 409)

    # ------------------------------------------------------------------
    # Response builders
    # ------------------------------------------------------------------

    def _response(self, risk: Risk) -> RiskResponse:
        return RiskResponse(
            id=risk.id,
            project_id=risk.project_id,
            rule_version_id=risk.rule_version_id,
            risk_type=risk.risk_type,
            severity=risk.severity,
            title=risk.title,
            description=risk.description,
            trigger_data=risk.trigger_data,
            confidence=risk.confidence,
            status=risk.status,
            resolution=risk.resolution,
            primary_evidence_id=risk.primary_evidence_id,
            evidence_ids=self._risks.list_evidence_ids(risk.id),
            created_at=risk.created_at,
            updated_at=risk.updated_at,
        )

    @staticmethod
    def _task_response(task: Any) -> TaskResponse:
        return TaskResponse(
            id=task.id,
            task_type=task.task_type,
            target_type=task.target_type,
            target_id=task.target_id,
            status=task.status,
            attempt=task.attempt,
            error_code=task.error_code,
            error_message=task.error_message,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _require_bound_enterprise_ids(self, project: TenderProject) -> list[UUID]:
        """Project must bind at least one bidding enterprise (consortium)."""
        enterprise_ids = self._projects.list_enterprise_ids(project.id)
        if not enterprise_ids:
            raise DomainError(
                "ENTERPRISE_NOT_BOUND",
                "项目未绑定投标企业,请先在项目设置中绑定",
                422,
            )
        return enterprise_ids

    def _state_hash(
        self,
        project_id: UUID,
        bid_deadline: dict[str, str | None],
        enterprise_ids: list[UUID],
    ) -> str:
        requirements = self._requirements.list_confirmed_for_project(project_id)
        materials = self._materials.list_confirmed_for_enterprises(enterprise_ids)
        current_matches = self._matches.list_current_for_project(project_id)

        value = {
            "project_id": str(project_id),
            "bid_deadline": bid_deadline,
            "enterprise_ids": sorted(str(eid) for eid in enterprise_ids),
            "requirements": [
                {
                    "id": str(req.id),
                    "updated_at": req.updated_at.isoformat(),
                    "conditions": req.conditions,
                    "mandatory": req.is_mandatory,
                    "status": req.review_status,
                }
                for req in requirements
            ],
            "materials": [
                {
                    "id": str(mat.id),
                    "updated_at": mat.updated_at.isoformat(),
                    "status": mat.status,
                    "valid_to": mat.valid_to.isoformat() if mat.valid_to else None,
                    "amount": str(mat.amount) if mat.amount is not None else None,
                    "attributes": mat.attributes,
                }
                for mat in materials
            ],
            # Match 结果直接影响风险判断（MANDATORY_EVIDENCE_MISSING 等），必须纳入状态哈希
            "matches": sorted(
                {
                    f"{m.requirement_id}:{m.material_id}": {
                        "final_status": m.final_status,
                        "is_overridden": m.is_overridden,
                        "updated_at": m.updated_at.isoformat(),
                    }
                    for m in current_matches
                }.values(),
                key=lambda x: x["updated_at"],
            ),
        }
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
