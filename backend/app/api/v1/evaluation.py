"""Administrator-only live retrieval evaluation endpoints."""

import json
from pathlib import Path
from time import perf_counter
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.config import get_settings
from app.core.errors import DomainError
from app.core.permissions import require_system_admin
from app.db.models.evaluation import EvaluationCase, EvaluationSet
from app.db.session import get_db_session
from app.integrations.ai.embedding import BgeM3Client
from app.integrations.ai.llm import DeepSeekV4FlashClient
from app.integrations.ai.reranker import BgeRerankerV2M3Client
from app.integrations.vector_store import PgVectorStore
from app.services.knowledge_rag_service import KnowledgeRagService
from app.services.query_rewrite_service import rewrite_query
from app.services.rag_service import RagService

router = APIRouter(prefix="/evaluations", tags=["evaluation"])
_SET_PATH = Path(__file__).resolve().parents[3] / "scripts" / "rag_eval_set.json"


class EvaluationCaseInput(BaseModel):
    """A manually maintained retrieval assertion."""

    question: str = Field(min_length=1, max_length=2000)
    scope: str = Field(pattern="^(knowledge|project)$")
    expected_evidence: list[str] = Field(min_length=1)


class EvaluationSetInput(BaseModel):
    """Editable set of retrieval assertions."""

    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=100)


def _not_found(message: str) -> None:
    raise DomainError("RESOURCE_NOT_FOUND", message, 404)


def _load_cases(session: Session, set_id: UUID) -> list[EvaluationCase]:
    return (
        session.query(EvaluationCase)
        .filter_by(set_id=set_id)
        .order_by(EvaluationCase.sort_order)
        .all()
    )


def _set_payload(item: EvaluationSet, cases: list[EvaluationCase]) -> dict:
    return {
        "id": str(item.id),
        "name": item.name,
        "description": item.description,
        "enabled": item.enabled,
        "version": item.version,
        "cases": [
            {
                "id": str(case.id),
                "question": case.question,
                "scope": case.scope,
                "expected_evidence": case.expected_evidence,
            }
            for case in cases
        ],
    }


def _create_cases(set_id: UUID, payload: EvaluationSetInput) -> list[EvaluationCase]:
    return [
        EvaluationCase(
            set_id=set_id,
            question=case.question,
            scope=case.scope,
            expected_evidence=case.expected_evidence,
            sort_order=index,
        )
        for index, case in enumerate(payload.cases)
    ]


@router.get("/sets")
def list_sets(
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[dict]:
    require_system_admin(current_user.role_codes)
    evaluation_sets = (
        session.query(EvaluationSet).order_by(EvaluationSet.created_at.desc()).all()
    )
    return [_set_payload(item, _load_cases(session, item.id)) for item in evaluation_sets]


@router.post("/sets", status_code=status.HTTP_201_CREATED)
def create_set(
    payload: EvaluationSetInput,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_system_admin(current_user.role_codes)
    item = EvaluationSet(
        name=payload.name,
        description=payload.description,
        created_by=current_user.id,
    )
    session.add(item)
    session.flush()
    cases = _create_cases(item.id, payload)
    session.add_all(cases)
    session.commit()
    return _set_payload(item, cases)


@router.put("/sets/{set_id}")
def update_set(
    set_id: UUID,
    payload: EvaluationSetInput,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_system_admin(current_user.role_codes)
    item = session.get(EvaluationSet, set_id)
    if item is None:
        _not_found("评测题集不存在")

    item.name = payload.name
    item.description = payload.description
    item.version += 1
    session.query(EvaluationCase).filter_by(set_id=item.id).delete()
    cases = _create_cases(item.id, payload)
    session.add_all(cases)
    session.commit()
    return _set_payload(item, cases)


@router.patch("/sets/{set_id}/enabled")
def set_enabled(
    set_id: UUID,
    enabled: bool,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    require_system_admin(current_user.role_codes)
    item = session.get(EvaluationSet, set_id)
    if item is None:
        _not_found("评测题集不存在")

    item.enabled = enabled
    session.commit()
    return _set_payload(item, _load_cases(session, item.id))


@router.delete("/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_set(
    set_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    require_system_admin(current_user.role_codes)
    item = session.get(EvaluationSet, set_id)
    if item is None:
        _not_found("评测题集不存在")

    session.delete(item)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _context_text(context: object) -> str:
    return str(
        getattr(context, "content", None)
        or getattr(getattr(context, "chunk", None), "content", "")
    )


def _load_entries(session: Session, set_id: UUID | None) -> list[dict[str, object]]:
    if set_id is None:
        return json.loads(_SET_PATH.read_text(encoding="utf-8"))

    selected = session.get(EvaluationSet, set_id)
    if selected is None or not selected.enabled:
        _not_found("评测题集不存在或已停用")
    return [
        {
            "question": case.question,
            "scope": case.scope,
            "expect": case.expected_evidence,
        }
        for case in _load_cases(session, set_id)
    ]


@router.post("/rag")
def run_rag_evaluation(
    project_id: UUID | None = Query(default=None),
    set_id: UUID | None = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> dict:
    """Run retrieval only; it does not call the answer-generation model."""
    require_system_admin(current_user.role_codes)
    entries = _load_entries(session, set_id)
    # The built-in set is useful without a project: in that mode it is a pure
    # legal-knowledge regression set. Project questions join only when a
    # concrete project is selected, so they never depress the legal score.
    if set_id is None and project_id is None:
        entries = [entry for entry in entries if entry["scope"] == "knowledge"]
    settings = get_settings()
    embedding = BgeM3Client(settings)
    reranker = BgeRerankerV2M3Client(settings)
    vector_store = PgVectorStore(settings)
    legal_rag = KnowledgeRagService(
        session, settings, embedding, vector_store, reranker, llm=None
    )
    project_rag = RagService(
        session,
        settings,
        embedding,
        vector_store,
        reranker,
        DeepSeekV4FlashClient(settings),
    )
    results: list[dict[str, object]] = []
    started = perf_counter()

    for entry in entries:
        question = str(entry["question"])
        scope = str(entry["scope"])
        expected = list(entry["expect"])
        try:
            if scope == "knowledge":
                vector = legal_rag._embed_question(question)
                contexts = legal_rag._rank(
                    question,
                    legal_rag._retrieve(
                        question,
                        vector,
                        None,
                        current_user.id,
                        current_user.role_codes,
                    ),
                )
            elif project_id is not None:
                rewritten = rewrite_query(question)
                vector = project_rag._embed_question(question, rewritten)
                contexts = project_rag._retrieve(
                    question,
                    vector,
                    project_id,
                    current_user.id,
                    current_user.role_codes,
                    rewritten,
                )
            else:
                results.append(
                    {
                        "question": question,
                        "scope": scope,
                        "passed": None,
                        "skipped": True,
                        "error": "未选择项目，项目范围题未执行",
                        "expected": expected,
                    }
                )
                continue

            matched_context = next(
                (
                    (index, context)
                    for index, context in enumerate(contexts[:5], start=1)
                    if any(
                        evidence.replace(" ", "")
                        in _context_text(context).replace(" ", "")
                        for evidence in expected
                    )
                ),
                None,
            )
            rank = None if matched_context is None else matched_context[0]
            excerpt = ""
            if matched_context is not None:
                excerpt = _context_text(matched_context[1])[:500]
            results.append(
                {
                    "question": question,
                    "scope": scope,
                    "passed": rank is not None,
                    "rank": rank,
                    "expected": expected,
                    "matched_excerpt": excerpt,
                }
            )
        except Exception as exc:  # Preserve per-case failures for diagnosis.
            results.append(
                {
                    "question": question,
                    "scope": scope,
                    "passed": False,
                    "error": str(exc)[:160],
                    "expected": expected,
                }
            )

    evaluated_results = [item for item in results if not item.get("skipped")]
    total = len(evaluated_results)
    passed = sum(bool(item["passed"]) for item in evaluated_results)
    return {
        "total": total,
        "passed": passed,
        "skipped": len(results) - total,
        "recall_at_5": round(passed / total, 4) if total else 0,
        "elapsed_ms": round((perf_counter() - started) * 1000),
        "results": results,
    }
