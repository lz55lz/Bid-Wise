"""评测运行的提交与 Worker 执行。"""

import logging
from time import monotonic

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.embedding import BgeM3EmbeddingClient
from app.integrations.task_queue import ArqTaskQueue, TaskQueueUnavailable
from app.modules.evaluation.models import EvaluationCase, EvaluationRun, EvaluationSet
from app.modules.evaluation.state_machine import transition
from app.modules.identity.service import AuthenticatedUser
from app.modules.knowledge.retrieval_service import KnowledgeRetrievalService
from app.modules.retrieval.candidate_service import EvidenceCandidateRetrievalService


logger = logging.getLogger(__name__)

class EvaluationService:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session, self._settings = session, settings

    async def submit(
        self, actor: AuthenticatedUser, set_id: UUID | None, project_id: UUID | None
    ) -> EvaluationRun:
        if "SYSTEM_ADMIN" not in actor.role_codes:
            raise DomainError("PERMISSION_DENIED", "仅系统管理员可运行评测", 403)
        if set_id is None:
            raise DomainError("VALIDATION_ERROR", "必须选择评测集", 422)
        selected = await self._session.get(EvaluationSet, set_id)
        if selected is None or not selected.enabled:
            raise DomainError("RESOURCE_NOT_FOUND", "评测集不存在或已停用", 404)
        run = EvaluationRun(
            set_id=set_id,
            project_id=project_id,
            status="QUEUED",
            result=None,
            error_message=None,
            created_by=actor.id,
            created_at=datetime.now(UTC),
            started_at=None,
            completed_at=None,
        )
        self._session.add(run)
        await self._session.commit()
        try:
            await ArqTaskQueue(self._settings).publish_evaluation(str(run.id))
        except TaskQueueUnavailable:
            # 评测运行仍保持 QUEUED，周期 reconciler 负责补投。
            logger.warning(
                "ARQ 暂不可用，评测任务等待 reconciler 补投 run_id=%s",
                run.id,
            )
        return run

    async def process(self, run_id: UUID) -> None:
        run = await self._session.get(EvaluationRun, run_id, with_for_update=True)
        if run is None or run.status != "QUEUED":
            return
        transition(run, "RUNNING")
        run.started_at = datetime.now(UTC)
        await self._session.commit()
        started = monotonic()
        try:
            cases = list(
                (
                    await self._session.scalars(
                        select(EvaluationCase)
                        .where(EvaluationCase.set_id == run.set_id)
                        .order_by(EvaluationCase.sort_order)
                    )
                ).all()
            )
            actor = AuthenticatedUser(
                id=run.created_by,
                username="evaluation",
                display_name="evaluation",
                role_codes=frozenset({"SYSTEM_ADMIN"}),
            )
            project_search = EvidenceCandidateRetrievalService(
                self._session, BgeM3EmbeddingClient(self._settings)
            )
            knowledge_search = KnowledgeRetrievalService(self._session, self._settings)
            results: list[dict[str, object]] = []
            for case in cases:
                if case.scope == "project":
                    if run.project_id is None:
                        results.append(
                            {
                                "question": case.question,
                                "scope": case.scope,
                                "skipped": True,
                                "reason": "未选择项目",
                            }
                        )
                        continue
                    hits = await project_search.search_project_evidences(
                        run.project_id, actor, case.question, 5
                    )
                    observed = [item.quoted_text for item in hits.items]
                else:
                    hits = await knowledge_search.search(case.question, 5)
                    observed = [str(item["content"]) for item in hits]
                match = next(
                    (
                        (rank, text)
                        for rank, text in enumerate(observed, start=1)
                        if any(
                            expected.replace(" ", "") in text.replace(" ", "")
                            for expected in case.expected_evidence
                        )
                    ),
                    None,
                )
                results.append(
                    {
                        "question": case.question,
                        "scope": case.scope,
                        "passed": match is not None,
                        "expected": case.expected_evidence,
                        "rank": None if match is None else match[0],
                        "matched_excerpt": None if match is None else match[1],
                    }
                )
            evaluated = [item for item in results if not item.get("skipped")]
            passed = sum(bool(item.get("passed")) for item in evaluated)
            run.result = {
                "total": len(evaluated),
                "passed": passed,
                "skipped": len(results) - len(evaluated),
                "recall_at_5": round(passed / len(evaluated), 4) if evaluated else 0,
                "elapsed_ms": round((monotonic() - started) * 1000),
                "mode": "controlled",
                "results": results,
            }
            transition(run, "SUCCEEDED")
            run.completed_at = datetime.now(UTC)
            await self._session.commit()
        except Exception as exc:
            await self._session.rollback()
            run = await self._session.get(EvaluationRun, run_id, with_for_update=True)
            if run is not None:
                transition(run, "FAILED")
                run.error_message = str(exc)[:1000]
                run.completed_at = datetime.now(UTC)
                await self._session.commit()
