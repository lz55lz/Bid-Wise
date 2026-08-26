from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_write_project_documents
from app.db.models import (
    EnterpriseMaterial,
    Evidence,
    MatchEvidence,
    MatchOverride,
    MatchResult,
    Requirement,
)
from app.db.repositories.match_repository import MatchRepository
from app.db.repositories.material_repository import MaterialRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.schemas.documents import TaskResponse
from app.schemas.matches import MatchOverrideRequest, MatchResponse
from app.services.audit_service import AuditService
from app.services.enterprise_tag_matcher import EnterpriseTagMatcher
from app.services.material_match_policy import is_enterprise_material_requirement
from app.services.project_fact_resolver import ProjectFactResolver
from app.services.project_service import ProjectService
from app.services.task_service import RUN_MATCH, TaskService

logger = logging.getLogger(__name__)


class MatchStatus(StrEnum):
    """Automatic matching outcome for a requirement-material pair."""

    MATCHED = "MATCHED"
    UNCERTAIN = "UNCERTAIN"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class _Evaluation:
    status: str
    reason: str
    missing_conditions: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class _EvidenceMaps:
    """Pre-loaded evidence/declaration mappings to avoid N+1 queries."""

    requirement: dict[UUID, list[UUID]]
    material: dict[UUID, list[UUID]]
    declaration: dict[UUID, bool]


class MatchingService:
    """Deterministic enterprise-tag matching; no model is allowed to promote a result."""

    # ── Material-type → requirement-category compatibility ──────────
    _MATERIAL_CATEGORY_MAP: dict[str, set[str]] = {
        "QUALIFICATION": {"QUALIFICATION"},
        "CERTIFICATE": {"QUALIFICATION", "SCORING"},
        "PROJECT_EXPERIENCE": {"QUALIFICATION"},
        "PERSONNEL": {"QUALIFICATION"},
    }

    # ── Keyword → acceptable material types ─────────────────────────
    _REQ_KEYWORD_MATERIAL_TYPES: dict[str, set[str]] = {
        "业绩": {"PROJECT_EXPERIENCE"},
        "合同": {"PROJECT_EXPERIENCE"},
        "竣工": {"PROJECT_EXPERIENCE"},
        "验收": {"PROJECT_EXPERIENCE"},
        "中标": {"PROJECT_EXPERIENCE"},
        "工程师": {"PERSONNEL", "CERTIFICATE"},
        "建造师": {"PERSONNEL"},
        "职称": {"PERSONNEL"},
        "资质": {"QUALIFICATION", "CERTIFICATE"},
        "营业执照": {"QUALIFICATION"},
        "安全": {"QUALIFICATION", "CERTIFICATE"},
        "审计": {"QUALIFICATION"},
        "财务": {"QUALIFICATION"},
        "报表": {"QUALIFICATION"},
        "人员": {"PERSONNEL"},
        "社保": {"PERSONNEL"},
        "证书": {"CERTIFICATE"},
        "认证": {"CERTIFICATE"},
        "体系": {"CERTIFICATE"},
    }

    _KEYWORD_HIT_THRESHOLD = 2

    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = ProjectService(session)
        self._requirements = RequirementRepository(session)
        self._materials = MaterialRepository(session)
        self._matches = MatchRepository(session)
        self._audit = AuditService(session)
        self._project_facts = ProjectFactResolver(session)
        self._tag_matcher = EnterpriseTagMatcher()

    # ==================================================================
    # Public API
    # ==================================================================

    def run(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
    ) -> list[MatchResponse]:
        project = self._require_writable_project(project_id, actor_id, role_codes)

        # 仅匹配项目绑定企业(联合体)的已确认材料，未绑定则拒绝执行
        enterprise_ids = self._require_bound_enterprise_ids(project)

        requirements = self._requirements.list_confirmed_for_project(project_id)
        materials = self._materials.list_confirmed_for_enterprises(enterprise_ids)
        bid_deadline = self._project_facts.resolve_bid_deadline(project).date

        # Pre-load evidence maps to avoid N+1 queries
        evidence_maps = self._load_evidence_maps(requirements, materials)

        self._matches.mark_not_current_for_project(project_id)

        for requirement in requirements:
            # Project facts and bidding conduct are not enterprise proofs.
            # Only categories with an explicit material-proof contract enter matching.
            if not is_enterprise_material_requirement(requirement):
                continue
            self._match_requirement(
                project_id, requirement, materials, evidence_maps, bid_deadline
            )

        self._audit.record(
            actor_id=actor_id,
            action=RUN_MATCH,
            target_type="PROJECT",
            target_id=project_id,
            project_id=project_id,
            after={"confirmed_requirements": len(requirements)},
        )
        self._session.commit()
        return self._build_responses(
            self._matches.list_current_for_project(project_id)
        )

    def _upsert_match_result(
        self,
        project_id: UUID,
        requirement: Requirement,
        material: EnterpriseMaterial | None,
        final_status: str,
        reason: str,
        evidence_ids: list[UUID],
    ) -> None:
        """写入或更新单条匹配结果（内部方法）。"""
        material_id = None if material is None else material.id
        result = self._matches.find_pair(requirement.id, material_id, for_update=True)
        now = datetime.now(UTC)

        if result is None:
            result = MatchResult(
                id=uuid4(),
                project_id=project_id,
                requirement_id=requirement.id,
                material_id=material_id,
                automatic_status=final_status,
                final_status=final_status,
                reason=reason,
                missing_conditions=[],
                is_overridden=False,
                is_current=True,
                created_at=now,
                updated_at=now,
            )
            self._matches.add(result)
            self._session.flush()
        else:
            result.automatic_status = final_status
            result.final_status = final_status
            result.reason = reason
            result.is_current = True
            result.updated_at = now

        # 写入证据链
        existing_links = set(self._matches.list_evidence_links(result.id))
        pending_links = [(eid, "MATERIAL") for eid in evidence_ids]
        for evidence_id, side in pending_links:
            if (evidence_id, side) not in existing_links:
                self._matches.add_evidence(
                    MatchEvidence(
                        match_result_id=result.id,
                        evidence_id=evidence_id,
                        side=side,
                    )
                )

    def submit(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        publisher: Any,
    ) -> TaskResponse:
        project = self._require_writable_project(project_id, actor_id, role_codes)
        enterprise_ids = self._require_bound_enterprise_ids(project)

        task = TaskService(self._session).create_match_task(
            project_id,
            actor_id,
            self._state_hash(project_id, enterprise_ids),
        )
        self._session.commit()

        if task.status == "QUEUED" and task.celery_task_id is None:
            try:
                task.celery_task_id = publisher.publish_run_match(task.id, project_id)
                self._session.commit()
            except Exception:
                self._session.rollback()
                TaskService(self._session).fail_project_task(
                    task.id,
                    RUN_MATCH,
                    "TASK_QUEUE_UNAVAILABLE",
                    "材料匹配任务队列暂不可用，请稍后重试。",
                )

        return self._task_response(task)

    def list(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
    ) -> list[MatchResponse]:
        self._projects.get_visible(project_id, actor_id, role_codes)
        results = self._matches.list_current_for_project(project_id)
        return self._build_responses(results)

    def override(
        self,
        match_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: MatchOverrideRequest,
    ) -> MatchResponse:
        result = self._matches.get(match_id, for_update=True)
        if result is None:
            raise DomainError("RESOURCE_NOT_FOUND", "匹配结果不存在", 404)

        self._require_writable_project(
            result.project_id,
            actor_id,
            role_codes,
            permission_error_msg="无权覆盖匹配结果",
        )

        now = datetime.now(UTC)
        previous_status = result.final_status

        result.final_status = payload.final_status
        result.is_overridden = True
        result.updated_at = now

        self._matches.add_override(
            MatchOverride(
                id=uuid4(),
                match_result_id=result.id,
                previous_status=previous_status,
                final_status=payload.final_status,
                override_reason=payload.reason,
                overridden_by=actor_id,
                overridden_at=now,
            )
        )

        confirmation = self._create_confirmation_evidence(
            result.id, payload.reason, actor_id, now
        )
        self._matches.add_evidence(
            MatchEvidence(
                match_result_id=result.id,
                evidence_id=confirmation.id,
                side="MISSING",
            )
        )

        self._audit.record(
            actor_id=actor_id,
            action="OVERRIDE_MATCH",
            target_type="MATCH_RESULT",
            target_id=result.id,
            project_id=result.project_id,
            before={"final_status": previous_status},
            after={"final_status": result.final_status},
        )
        self._session.commit()
        return self._response(result)

    def override_in_project(
        self,
        project_id: UUID,
        match_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: MatchOverrideRequest,
    ) -> MatchResponse:
        """Project-scoped override: reject resources that don't belong to the project."""
        result = self._matches.get(match_id)
        if result is None or result.project_id != project_id:
            raise DomainError("RESOURCE_NOT_FOUND", "资源不存在", 404)
        return self.override(match_id, actor_id, role_codes, payload)

    # ==================================================================
    # Matching logic
    # ==================================================================

    def _match_requirement(
        self,
        project_id: UUID,
        requirement: Requirement,
        materials: list[EnterpriseMaterial],
        evidence_maps: _EvidenceMaps,
        bid_deadline: date | None,
    ) -> None:
        candidates = self._tag_matcher.recall(requirement, materials)

        if not candidates:
            self._upsert(
                project_id,
                requirement,
                None,
                _Evaluation(MatchStatus.MISSING, "企业标签库中未召回可比较标签", []),
                evidence_maps,
            )
            return

        evaluated = [
            (
                material,
                self._evaluate(requirement, material, bid_deadline),
                len(candidates) - index,
            )
            for index, material in enumerate(candidates)
        ]
        status_rank = {
            MatchStatus.MATCHED: 2,
            MatchStatus.UNCERTAIN: 1,
            MatchStatus.MISSING: 0,
        }
        material, evaluation, _recall_rank = max(
            evaluated,
            key=lambda item: (
                status_rank[MatchStatus(item[1].status)],
                item[2],
                str(item[0].id),
            ),
        )
        self._upsert(project_id, requirement, material, evaluation, evidence_maps)

    def _evaluate(
        self,
        requirement: Requirement,
        material: EnterpriseMaterial,
        bid_deadline: date | None,
    ) -> _Evaluation:
        """Compare tender conditions with trusted enterprise tags, not file presence."""
        result = self._tag_matcher.evaluate(requirement, material, bid_deadline)
        return _Evaluation(result.status, result.reason, result.missing_conditions)

    @classmethod
    def _compatible_material_types(
        cls, requirement: Requirement, material: EnterpriseMaterial
    ) -> bool:
        """关键词 + material_type 双层过滤：至少 N 个关键词命中才算相关。"""
        mat_type = material.material_type

        # 1. category 基本兼容
        if requirement.category not in cls._MATERIAL_CATEGORY_MAP.get(mat_type, set()):
            return False

        # 2. 统计命中数：req 有该词 + material 名也有该词 + 类型匹配
        req_text = f"{requirement.title or ''} {requirement.description or ''}"
        mat_text = material.name or ""
        hit_count = sum(
            1
            for kw, types in cls._REQ_KEYWORD_MATERIAL_TYPES.items()
            if kw in req_text and mat_type in types and kw in mat_text
        )
        return hit_count >= cls._KEYWORD_HIT_THRESHOLD

    # ==================================================================
    # Upsert / persistence
    # ==================================================================

    def _upsert(
        self,
        project_id: UUID,
        requirement: Requirement,
        material: EnterpriseMaterial | None,
        evaluation: _Evaluation,
        evidence_maps: _EvidenceMaps,
    ) -> None:
        material_id = None if material is None else material.id
        result = self._matches.find_pair(requirement.id, material_id, for_update=True)
        now = datetime.now(UTC)

        if result is None:
            result = MatchResult(
                id=uuid4(),
                project_id=project_id,
                requirement_id=requirement.id,
                material_id=material_id,
                automatic_status=evaluation.status,
                final_status=evaluation.status,
                reason=evaluation.reason,
                missing_conditions=evaluation.missing_conditions,
                is_overridden=False,
                is_current=True,
                created_at=now,
                updated_at=now,
            )
            self._matches.add(result)
            self._session.flush()
        else:
            result.automatic_status = evaluation.status
            result.reason = evaluation.reason
            result.missing_conditions = evaluation.missing_conditions
            result.is_current = True
            result.updated_at = now
            if not result.is_overridden:
                result.final_status = evaluation.status

        self._link_evidence(result, requirement, material, evidence_maps)

    def _link_evidence(
        self,
        result: MatchResult,
        requirement: Requirement,
        material: EnterpriseMaterial | None,
        evidence_maps: _EvidenceMaps,
    ) -> None:
        existing_links = set(self._matches.list_evidence_links(result.id))

        req_ids = evidence_maps.requirement.get(requirement.id, [])
        # 招标要求自身若有关联 Evidence 但 RequirementEvidence 为空，
        # 使用 primary_evidence_id 兜底
        if not req_ids and requirement.primary_evidence_id is not None:
            req_ids = [requirement.primary_evidence_id]

        mat_ids: list[UUID] = (
            [] if material is None else evidence_maps.material.get(material.id, [])
        )

        pending_links: list[tuple[UUID, str]] = []
        pending_links.extend((eid, "REQUIREMENT") for eid in req_ids)
        pending_links.extend((eid, "MATERIAL") for eid in mat_ids)

        for evidence_id, side in pending_links:
            if (evidence_id, side) not in existing_links:
                self._matches.add_evidence(
                    MatchEvidence(
                        match_result_id=result.id,
                        evidence_id=evidence_id,
                        side=side,
                    )
                )

        if not req_ids and not mat_ids:
            logger.warning(
                "[Matching] match_result=%s requirement=%s has no traceable evidence",
                result.id,
                requirement.id,
            )

    # ==================================================================
    # Evidence / state helpers
    # ==================================================================

    def _load_evidence_maps(
        self,
        requirements: list[Requirement],
        materials: list[EnterpriseMaterial],
    ) -> _EvidenceMaps:
        requirement_ids = [r.id for r in requirements]
        material_ids = [m.id for m in materials]
        return _EvidenceMaps(
            requirement=(
                self._requirements.list_evidence_ids_for_requirements(requirement_ids)
                if requirement_ids
                else {}
            ),
            material=(
                self._materials.list_evidence_ids_for_materials(material_ids)
                if material_ids
                else {}
            ),
            # PROOF/DECLARED 自声明：企业声明"我已核对"也算满足 evidence 维度
            declaration=(
                self._materials.list_declaration_status(material_ids)
                if material_ids
                else {}
            ),
        )

    def _create_confirmation_evidence(
        self,
        result_id: UUID,
        reason: str,
        actor_id: UUID,
        now: datetime,
    ) -> Evidence:
        evidence = Evidence(
            id=uuid4(),
            source_type="USER_CONFIRMATION",
            document_version_id=None,
            document_node_id=None,
            page_number=None,
            quoted_text=reason,
            content_hash=hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            bbox=None,
            source_reference={"match_result_id": str(result_id)},
            created_at=now,
            created_by=actor_id,
        )
        self._session.add(evidence)
        return evidence

    def _state_hash(self, project_id: UUID, enterprise_ids: list[UUID]) -> str:
        requirements = self._requirements.list_confirmed_for_project(project_id)
        materials = self._materials.list_confirmed_for_enterprises(enterprise_ids)
        current_matches = self._matches.list_current_for_project(project_id)

        value = {
            "enterprise_ids": sorted(str(eid) for eid in enterprise_ids),
            "requirements": [
                {
                    "id": str(r.id),
                    "updated_at": r.updated_at.isoformat(),
                    "conditions": r.conditions,
                    "status": r.review_status,
                }
                for r in requirements
            ],
            "materials": [
                {
                    "id": str(m.id),
                    "updated_at": m.updated_at.isoformat(),
                    "status": m.status,
                    "valid_to": m.valid_to.isoformat() if m.valid_to else None,
                    "amount": str(m.amount) if m.amount is not None else None,
                    "attributes": m.attributes,
                }
                for m in materials
            ],
            # Match 结果直接影响 risk_service 的判断，必须纳入状态哈希
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

    # ==================================================================
    # Response building
    # ==================================================================

    def _build_responses(self, results: list[MatchResult]) -> list[MatchResponse]:
        return [self._response(r) for r in results]

    def _response(self, result: MatchResult) -> MatchResponse:
        return MatchResponse(
            id=result.id,
            project_id=result.project_id,
            requirement_id=result.requirement_id,
            material_id=result.material_id,
            automatic_status=result.automatic_status,
            final_status=result.final_status,
            reason=result.reason,
            missing_conditions=result.missing_conditions,
            is_overridden=result.is_overridden,
            evidence_ids=[
                eid for eid, _ in self._matches.list_evidence_links(result.id)
            ],
            created_at=result.created_at,
            updated_at=result.updated_at,
        )

    # ==================================================================
    # Permission / project helpers
    # ==================================================================

    def _require_writable_project(
        self,
        project_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        *,
        permission_error_msg: str = "无权执行材料匹配",
    ):
        """Fetch visible project, enforce writability and document-write permission."""
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        if not can_write_project_documents(role_codes):
            raise DomainError("PERMISSION_DENIED", permission_error_msg, 403)
        return project

    def _require_bound_enterprise_ids(self, project) -> list[UUID]:
        """项目必须绑定至少一家投标企业(联合体)，否则无法界定材料匹配范围。"""
        enterprise_ids = self._projects.list_enterprise_ids(project.id)
        if not enterprise_ids:
            raise DomainError(
                "ENTERPRISE_NOT_BOUND",
                "项目未绑定投标企业,请先在项目设置中绑定",
                422,
            )
        return enterprise_ids

    # ==================================================================
    # Task response
    # ==================================================================

    @staticmethod
    def _task_response(task) -> TaskResponse:
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
