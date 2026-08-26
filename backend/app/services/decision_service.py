from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.errors import DomainError
from app.core.permissions import can_write_project_documents
from app.db.models import Decision, DecisionEvidence, Evidence
from app.db.repositories.decision_repository import DecisionRepository
from app.db.repositories.match_repository import MatchRepository
from app.db.repositories.requirement_repository import RequirementRepository
from app.db.repositories.risk_repository import RiskRepository
from app.schemas.decisions import DecisionResponse
from app.schemas.documents import TaskResponse
from app.services.audit_service import AuditService
from app.services.project_fact_resolver import ProjectFactResolver
from app.services.project_service import ProjectService
from app.services.task_service import RUN_DECISION, TaskService

logger = logging.getLogger(__name__)


class DecisionService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._projects = ProjectService(session)
        self._decisions = DecisionRepository(session)
        self._risks = RiskRepository(session)
        self._matches = MatchRepository(session)
        self._requirements = RequirementRepository(session)
        self._audit = AuditService(session)
        self._project_facts = ProjectFactResolver(session)

    def generate(self, project_id: UUID, actor_id: UUID, role_codes: set[str]) -> DecisionResponse:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        self._require_writer(role_codes)
        risks = self._risks.list_current_for_project(project_id)
        matches = self._matches.list_current_for_project(project_id)
        now = datetime.now(UTC)
        deadline = self._project_facts.resolve_bid_deadline(project)
        deadline_expired = deadline.is_expired(now)
        qualification_unmet = any(
            risk.status == "CONFIRMED"
            and risk.risk_type == "QUALIFICATION"
            and risk.severity in {"CRITICAL", "HIGH"}
            for risk in risks
        )
        unresolved_critical = any(
            risk.status in {"PENDING", "CONFIRMED"} and risk.severity == "CRITICAL"
            for risk in risks
        )
        missing = [
            {
                "match_result_id": str(match.id),
                "requirement_id": str(match.requirement_id),
                "material_id": None if match.material_id is None else str(match.material_id),
                "status": match.final_status,
                "reason": match.reason,
            }
            for match in matches
            if match.final_status in {"MISSING", "UNCERTAIN"}
        ]
        high_risks = sum(
            1
            for risk in risks
            if risk.status in {"PENDING", "CONFIRMED"} and risk.severity == "HIGH"
        )
        hard = {
            "deadline_expired": deadline_expired,
            "deadline_confirmed": deadline.is_confirmed,
            "deadline_precision": deadline.precision,
            "confirmed_qualification_unmet": qualification_unmet,
            "unresolved_critical_risk": unresolved_critical,
        }
        if deadline_expired:
            suggestion, reason = "REJECT", "投标截止时间已过，建议停止推进并由负责人确认最终处理。"
        elif not deadline.is_confirmed:
            suggestion, reason = "CAUTION", "尚未确认投标截止时间，需补充关键信息后再作最终决策。"
        elif deadline.precision != "DATETIME":
            suggestion, reason = (
                "CAUTION",
                "已确认投标截止日期，但具体截止时刻未确认，需补充后再作最终决策。",
            )
        elif qualification_unmet:
            suggestion, reason = (
                "REJECT",
                "存在已确认的资格不满足风险，建议补充核查后由负责人确认最终处理。",
            )
        elif unresolved_critical:
            suggestion, reason = "HOLD", "存在未处理的严重风险，建议先完成核查和处置。"
        elif missing:
            suggestion, reason = "CAUTION", "存在缺失、过期或待人工确认的材料匹配项，建议补充证明。"
        elif high_risks:
            suggestion, reason = "CAUTION", "存在待处理的高风险提示，建议核查后再作最终决定。"
        else:
            suggestion, reason = (
                "RECOMMEND",
                "当前未发现硬约束冲突，建议继续人工核查并由负责人作最终决定。",
            )
        evidence_ids = self._collect_evidence(project_id, risks, matches)
        evidence_ids = list(dict.fromkeys([*deadline.evidence_ids, *evidence_ids]))
        if not evidence_ids:
            logger.warning(
                "[Decision] project=%s no evidence collected, creating system evidence",
                project_id,
            )
            evidence_ids = [self._create_system_evidence(project_id, actor_id).id]
        else:
            logger.info(
                "[Decision] project=%s collected %d evidence ids",
                project_id,
                len(evidence_ids),
            )
        decision = Decision(
            id=uuid4(),
            project_id=project_id,
            suggestion=suggestion,
            hard_constraint_result=hard,
            reason=reason,
            missing_materials=missing,
            final_decision=None,
            confirmed_by=None,
            confirmed_at=None,
            created_at=now,
            created_by=actor_id,
        )
        self._decisions.add(decision)
        self._session.flush()
        for evidence_id in dict.fromkeys(evidence_ids):
            self._decisions.add_evidence(
                DecisionEvidence(decision_id=decision.id, evidence_id=evidence_id)
            )
        self._audit.record(
            actor_id=actor_id,
            action="GENERATE_DECISION",
            target_type="DECISION",
            target_id=decision.id,
            project_id=project_id,
            after={"suggestion": suggestion},
        )
        self._session.commit()
        return self._response(decision)

    def submit(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str], publisher
    ) -> TaskResponse:
        project = self._projects.get_visible(project_id, actor_id, role_codes)
        self._projects.require_writable(project)
        self._require_writer(role_codes)
        deadline = self._project_facts.resolve_bid_deadline(project)
        task = TaskService(self._session).create_decision_task(
            project_id,
            actor_id,
            self._state_hash(project_id, deadline.fingerprint()),
        )
        self._session.commit()
        if task.status == "QUEUED" and task.celery_task_id is None:
            try:
                task.celery_task_id = publisher.publish_generate_decision(task.id, project_id)
                self._session.commit()
            except Exception:
                self._session.rollback()
                TaskService(self._session).fail_project_task(
                    task.id,
                    RUN_DECISION,
                    "TASK_QUEUE_UNAVAILABLE",
                    "决策任务队列暂不可用，请稍后重试。",
                )
        return self._task_response(task)

    def latest(
        self, project_id: UUID, actor_id: UUID, role_codes: set[str]
    ) -> DecisionResponse | None:
        self._projects.get_visible(project_id, actor_id, role_codes)
        decision = self._decisions.latest_for_project(project_id)
        return None if decision is None else self._response(decision)

    def _collect_evidence(self, project_id: UUID, risks, matches) -> list[UUID]:
        requirements = self._requirements.list_confirmed_for_project(project_id)
        requirement_ids = [r.id for r in requirements]
        risk_ids = [r.id for r in risks]
        match_ids = [m.id for m in matches]

        requirement_evidence_map = (
            self._requirements.list_evidence_ids_for_requirements(requirement_ids)
            if requirement_ids
            else {}
        )
        risk_evidence_map = (
            self._risks.list_evidence_ids_for_risks(risk_ids) if risk_ids else {}
        )
        match_evidence_map = (
            self._matches.list_evidence_links_for_matches(match_ids)
            if match_ids
            else {}
        )

        ids: list[UUID] = []
        for _req_id, eids in requirement_evidence_map.items():
            ids.extend(eids)
        for _risk_id, eids in risk_evidence_map.items():
            ids.extend(eids)
        for _match_id, links in match_evidence_map.items():
            ids.extend(evidence_id for evidence_id, _ in links)

        deduped = list(dict.fromkeys(ids))
        logger.info(
            "[Decision] project=%s sources: req=%d risk=%d match=%d total=%d",
            project_id,
            len(requirement_evidence_map),
            len(risk_evidence_map),
            len(match_evidence_map),
            len(deduped),
        )
        return deduped

    def _create_system_evidence(self, project_id: UUID, actor_id: UUID) -> Evidence:
        text = f"decision:{project_id}"
        evidence = Evidence(
            id=uuid4(),
            source_type="SYSTEM_RULE",
            document_version_id=None,
            document_node_id=None,
            page_number=None,
            quoted_text="系统决策条件汇总",
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            bbox=None,
            source_reference={"project_id": str(project_id)},
            created_at=datetime.now(UTC),
            created_by=actor_id,
        )
        self._session.add(evidence)
        return evidence

    def _state_hash(self, project_id: UUID, deadline: dict[str, str | None]) -> str:
        requirements = self._requirements.list_confirmed_for_project(project_id)
        state = {
            "project_id": str(project_id),
            "deadline": deadline,
            "requirements": [
                {
                    "id": str(r.id),
                    "updated_at": r.updated_at.isoformat(),
                    "review_status": r.review_status,
                    "conditions": r.conditions,
                }
                for r in requirements
            ],
            "risks": [
                {
                    "id": str(item.id),
                    "updated_at": item.updated_at.isoformat(),
                    "status": item.status,
                }
                for item in self._risks.list_current_for_project(project_id)
            ],
            "matches": [
                {
                    "id": str(item.id),
                    "updated_at": item.updated_at.isoformat(),
                    "status": item.final_status,
                }
                for item in self._matches.list_current_for_project(project_id)
            ],
        }
        return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _require_writer(role_codes: set[str]) -> None:
        if not can_write_project_documents(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权生成投标建议。", 403)

    def _response(self, decision: Decision) -> DecisionResponse:
        return DecisionResponse(
            id=decision.id,
            project_id=decision.project_id,
            suggestion=decision.suggestion,
            hard_constraint_result=decision.hard_constraint_result,
            reason=decision.reason,
            missing_materials=decision.missing_materials,
            evidence_ids=self._decisions.list_evidence_ids(decision.id),
            created_at=decision.created_at,
            created_by=decision.created_by,
        )

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
