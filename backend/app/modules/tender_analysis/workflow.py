"""bid_pipeline 的 LangGraph 拓扑与节点级人工复核语义。

图只负责状态流转。节点使用的解析、候选召回、模型抽取与持久化服务必须从外部注入；
这样既能让 ARQ Worker 运行，也能避免把数据库 Session 或模型客户端放进图状态。
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal, NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class TenderPipelineState(TypedDict):
    """图中的可序列化状态；大文本只在节点内读取，不写入 checkpoint。"""

    pipeline_run_id: str
    document_version_id: str
    # 每个结构化 Evidence 的受控候选标签；extract 只提交命中的证据及其候选字段。
    evidence_candidate_tags: NotRequired[dict[str, list[str]]]
    candidate_tag_codes: NotRequired[list[str]]
    extracted_tags: NotRequired[dict[str, dict[str, object]]]
    # 单个模型批次耗尽格式重试后的受控摘要；不能让一批坏 JSON 丢掉其他批次结果。
    extraction_failures: NotRequired[list[dict[str, object]]]
    validation_issues: NotRequired[list[str]]
    review_tag_codes: NotRequired[list[str]]
    auto_approved_tag_codes: NotRequired[list[str]]
    approved_tag_codes: NotRequired[list[str]]
    reviewed_tags: NotRequired[dict[str, dict[str, object]]]
    review_outcome: NotRequired[Literal["approved", "rejected"]]
    # 审批人来自受鉴权 API 写入的恢复载荷，不能由模型或文档内容构造。
    reviewed_by: NotRequired[str]


PipelineNode = Callable[[TenderPipelineState], Awaitable[dict[str, Any]]]


async def human_review_node(state: TenderPipelineState) -> dict[str, Any]:
    """仅在关键字段异常时暂停；其余已校验字段自动进入项目事实。"""
    automatic_codes = {
        str(code) for code in state.get("auto_approved_tag_codes", [])
        if isinstance(code, str)
    }
    if not state.get("review_tag_codes"):
        return {
            "reviewed_tags": {
                code: dict(item) for code, item in state.get("extracted_tags", {}).items()
                if code in automatic_codes and isinstance(item, dict)
            },
            "review_outcome": "approved",
            "reviewed_by": None,
        }
    decision = interrupt(
        {
            "kind": "bid_validation_review",
            "pipeline_run_id": state["pipeline_run_id"],
            "document_version_id": state["document_version_id"],
            "validation_issues": state.get("validation_issues", []),
            "pending_review": state.get("extracted_tags", {}),
        }
    )
    if not isinstance(decision, dict) or decision.get("decision") not in {"approved", "rejected"}:
        raise ValueError("人工复核决定必须为 approved 或 rejected")
    reviewed_tags = decision.get("reviewed_tags", {})
    if not isinstance(reviewed_tags, dict):
        raise ValueError("人工复核标签必须是对象")
    approved_codes = decision.get("approved_tag_codes")
    if approved_codes is not None and not isinstance(approved_codes, list):
        raise ValueError("人工确认字段必须是数组")
    # 旧恢复载荷未带勾选项时保持全量确认；新页面只采纳明确勾选的候选。
    selected_codes = automatic_codes | (
        {str(code) for code in approved_codes}
        if isinstance(approved_codes, list)
        else set(state.get("review_tag_codes", []))
    )
    if decision["decision"] == "approved":
        approved_tags = {
            code: dict(item) if isinstance(item, dict) else {}
            for code, item in state.get("extracted_tags", {}).items()
            if code in selected_codes
        }
        for code, human_item in reviewed_tags.items():
            if not isinstance(human_item, dict):
                continue
            merged = dict(approved_tags.get(code, {}))
            merged.update(human_item)
            merged["human_edited"] = True
            approved_tags[code] = merged
    else:
        approved_tags = {}
    reviewer_id = decision.get("reviewer_id")
    return {
        "reviewed_tags": approved_tags,
        "review_outcome": decision["decision"],
        "reviewed_by": str(reviewer_id) if reviewer_id else None,
    }


def _with_failure_mark(
    stage_name: str,
    node: PipelineNode,
    on_node_error: Callable[[str, str, BaseException], Awaitable[None]] | None,
) -> PipelineNode:
    """给节点包一层失败回调；没有回调时直接返回原节点，不增加任何开销。"""
    if on_node_error is None:
        return node

    async def wrapped(state: TenderPipelineState) -> dict[str, Any]:
        try:
            return await node(state)
        except BaseException as exc:  # noqa: BLE001 - 记录后必须原样抛出
            await on_node_error(state["pipeline_run_id"], stage_name, exc)
            raise

    return wrapped


def build_tender_pipeline_graph(
    *,
    preflight: PipelineNode,
    select_candidates: PipelineNode,
    extract: PipelineNode,
    validate: PipelineNode,
    finalize: PipelineNode,
    on_node_error: Callable[[str, str, BaseException], Awaitable[None]] | None = None,
) -> StateGraph:
    """构造完整核心路径，节点实现由应用服务注入。

    ``preflight`` 只确认 Document Ingestion 已 READY；``select_candidates`` 把原先
    连续的分类/标签候选实现细节收成一个确定性阶段。``finalize`` 在人工恢复后统一写入
    审计标签与确认事实。图只保留真正需要观测或 checkpoint 的业务步骤。

    ``on_node_error`` 在节点抛出未处理异常时回调，由调用方用独立事务固化失败阶段，
    避免整个任务回滚后阶段状态全部停留在 PENDING。
    """
    graph = StateGraph(TenderPipelineState)
    graph.add_node("preflight", _with_failure_mark("preflight", preflight, on_node_error))
    graph.add_node(
        "select_candidates",
        _with_failure_mark("select_candidates", select_candidates, on_node_error),
    )
    graph.add_node("extract", _with_failure_mark("extract", extract, on_node_error))
    graph.add_node("validate", _with_failure_mark("validate", validate, on_node_error))
    graph.add_node("human_review", human_review_node)
    graph.add_node("finalize", _with_failure_mark("finalize", finalize, on_node_error))
    graph.add_edge(START, "preflight")
    graph.add_edge("preflight", "select_candidates")
    graph.add_edge("select_candidates", "extract")
    graph.add_edge("extract", "validate")
    graph.add_edge("validate", "human_review")
    graph.add_edge("human_review", "finalize")
    graph.add_edge("finalize", END)
    return graph
