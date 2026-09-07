"""管理员维护 RAG 评测集。"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.api.deps import ApplicationSettings, CurrentUser, DatabaseSession
from app.core.errors import DomainError
from app.modules.evaluation.models import EvaluationCase, EvaluationRun, EvaluationSet
from app.modules.evaluation.service import EvaluationService

router = APIRouter(prefix="/evaluations", tags=["评测中心"])


class EvaluationCaseInput(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    scope: str = Field(pattern="^(knowledge|project)$")
    expected_evidence: list[str] = Field(min_length=1)


class EvaluationSetInput(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=2000)
    cases: list[EvaluationCaseInput] = Field(min_length=1, max_length=100)


class EvaluationRunRequest(BaseModel):
    set_id: UUID | None = None
    project_id: UUID | None = None


def admin(user: CurrentUser) -> None:
    if "SYSTEM_ADMIN" not in user.role_codes:
        raise DomainError("PERMISSION_DENIED", "仅系统管理员可维护评测集", 403)


def _set_response(item: EvaluationSet, cases: list[EvaluationCase]) -> dict[str, object]:
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


@router.get("/sets")
async def list_sets(current_user: CurrentUser, session: DatabaseSession) -> list[dict[str, object]]:
    admin(current_user)
    sets = (
        await session.scalars(select(EvaluationSet).order_by(EvaluationSet.created_at.desc()))
    ).all()
    case_rows = (
        await session.scalars(select(EvaluationCase).order_by(EvaluationCase.sort_order))
    ).all()
    cases_by_set: dict[UUID, list[EvaluationCase]] = {}
    for case in case_rows:
        cases_by_set.setdefault(case.set_id, []).append(case)
    return [_set_response(item, cases_by_set.get(item.id, [])) for item in sets]


@router.post("/sets", status_code=status.HTTP_201_CREATED)
async def create_set(
    payload: EvaluationSetInput, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    admin(current_user)
    now = datetime.now(UTC)
    item = EvaluationSet(
        id=uuid4(),
        name=payload.name,
        description=payload.description,
        enabled=True,
        version=1,
        created_by=current_user.id,
        created_at=now,
    )
    session.add(item)
    # 模型间未声明 ORM relationship，SQLAlchemy 不会从 set_id 的纯 UUID 值
    # 推导出插入顺序。先 flush 父记录，确保评测样例的外键已有目标行。
    await session.flush()
    session.add_all(
        EvaluationCase(
            id=uuid4(),
            set_id=item.id,
            question=x.question,
            scope=x.scope,
            expected_evidence=x.expected_evidence,
            sort_order=i,
        )
        for i, x in enumerate(payload.cases)
    )
    # 该接口没有经过领域服务，必须在返回成功响应前明确提交。不能依赖请求
    # 依赖项的收尾阶段，否则客户端会拿到“已创建”的 ID，而数据仍可能随会话
    # 关闭被回滚，后续提交评测运行便会找不到评测集。
    await session.commit()
    return _set_response(
        item,
        list((await session.scalars(select(EvaluationCase).where(EvaluationCase.set_id == item.id))).all()),
    )


@router.put("/sets/{set_id}")
async def update_set(
    set_id: UUID,
    payload: EvaluationSetInput,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, object]:
    admin(current_user)
    item = await session.get(EvaluationSet, set_id, with_for_update=True)
    if item is None:
        raise DomainError("RESOURCE_NOT_FOUND", "评测集不存在", 404)
    item.name = payload.name
    item.description = payload.description
    item.version += 1
    await session.execute(delete(EvaluationCase).where(EvaluationCase.set_id == item.id))
    cases = [
        EvaluationCase(
            id=uuid4(), set_id=item.id, question=case.question, scope=case.scope,
            expected_evidence=case.expected_evidence, sort_order=index,
        )
        for index, case in enumerate(payload.cases)
    ]
    session.add_all(cases)
    await session.commit()
    return _set_response(item, cases)


@router.patch("/sets/{set_id}/enabled")
async def set_enabled(
    set_id: UUID,
    enabled: bool,
    current_user: CurrentUser,
    session: DatabaseSession,
) -> dict[str, object]:
    admin(current_user)
    item = await session.get(EvaluationSet, set_id, with_for_update=True)
    if item is None:
        raise DomainError("RESOURCE_NOT_FOUND", "评测集不存在", 404)
    item.enabled = enabled
    await session.commit()
    return _set_response(
        item,
        list((await session.scalars(select(EvaluationCase).where(EvaluationCase.set_id == item.id))).all()),
    )


@router.delete("/sets/{set_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_set(set_id: UUID, current_user: CurrentUser, session: DatabaseSession) -> None:
    admin(current_user)
    item = await session.get(EvaluationSet, set_id, with_for_update=True)
    if item is None:
        raise DomainError("RESOURCE_NOT_FOUND", "评测集不存在", 404)
    await session.delete(item)
    await session.commit()


@router.post("/runs", status_code=status.HTTP_202_ACCEPTED)
async def submit_run(
    payload: EvaluationRunRequest,
    current_user: CurrentUser,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> dict[str, object]:
    run = await EvaluationService(session, settings).submit(
        current_user, payload.set_id, payload.project_id
    )
    return {"id": str(run.id), "status": run.status}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: UUID, current_user: CurrentUser, session: DatabaseSession
) -> dict[str, object]:
    admin(current_user)
    run = await session.get(EvaluationRun, run_id)
    if run is None:
        raise DomainError("RESOURCE_NOT_FOUND", "评测运行不存在", 404)
    return {
        "id": str(run.id),
        "status": run.status,
        "result": run.result,
        "error_message": run.error_message,
    }


@router.get("/runs")
async def list_runs(current_user: CurrentUser, session: DatabaseSession) -> list[dict[str, object]]:
    admin(current_user)
    rows = (
        await session.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()))
    ).all()
    return [
        {
            "id": str(row.id),
            "status": row.status,
            "result": row.result,
            "error_message": row.error_message,
        }
        for row in rows
    ]
