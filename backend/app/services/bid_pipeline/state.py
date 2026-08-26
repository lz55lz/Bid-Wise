"""Bid Pipeline State - LangGraph 状态机定义

所有节点间共享的状态结构，使用 TypedDict + LangGraph StateGraph。

L1 粗筛（L1Annotate）：
  annotations: dict[section_path, list[category_code]]  — 每个章节分配 1-N 个分类

L2 精筛（L2Tagging）：
  candidate_tags: dict[chunk_id, list[tag_code]]  — 每个 chunk 分配置信度最高的候选标签

L3 召回（L3Extract）：
  extracted_tags: dict[tag_code, ExtractedTag]  — 最终提取结果

子图 extract_subgraph（召回→提取→校验）：
  recall_tags / extract_tags / validate_tags 三阶段

Fan-out 两路并行（risk_check / trap_detect）：
  risk_results / trap_results

Human-in-the-loop：
  pending_review: list[tag_code]  — 等待人工确认的标签
"""

from typing import Annotated, Any, Literal, NotRequired, TypedDict
from uuid import UUID

# -------------------------------------------------------------------
# Reducers
# -------------------------------------------------------------------


def _last_writer_wins(current, new):
    """Reducer: 并行写入时取最后一个更新的值"""
    return new if new is not None else current


def _merge_dict(current: dict | None, new: dict | None) -> dict:
    """Reducer: 合并 dict（多 Send 并发写入时逐个 key 合并）"""
    if current is None:
        current = {}
    if new is None:
        new = {}
    merged = current.copy()
    merged.update(new)
    return merged


def _merge_risk_results(current: list | None, new: list | None) -> list:
    """Reducer: 并行写入时合并风险结果列表（concat，去重）"""
    if current is None:
        current = []
    if new is None:
        new = []
    seen = {r.get("risk_title", "") + "||" + r.get("risk_type", "") for r in current}
    for r in new:
        key = r.get("risk_title", "") + "||" + r.get("risk_type", "")
        if key not in seen:
            current = current + [r]
            seen.add(key)
    return current


def _merge_stage_status(current: dict | None, new: dict | None) -> dict:
    """Reducer: 合并 stage_status dict"""
    if current is None:
        current = {}
    if new is None:
        new = {}
    merged = current.copy()
    merged.update(new)
    return merged


# -------------------------------------------------------------------
# 基础类型
# -------------------------------------------------------------------


class ChunkInfo(TypedDict):
    """文档分块信息（chunk_id = document_nodes.id，UUID 字符串）"""

    chunk_id: str
    doc_id: int
    chunk_index: int
    page_no: NotRequired[int | None]
    section_path: str
    chunk_text: str
    chunk_type: str
    # L1 分配的分类
    category_codes: NotRequired[list[str]]
    # L2 分配的候选标签
    candidate_tags: NotRequired[list[str]]


class ExtractedTag(TypedDict):
    """从文档中提取的标签值"""

    tag_code: str
    tag_name: str
    tag_value: str | list | dict | None
    confidence: float  # 0.0-1.0
    source_text: str
    source_chunk_id: str  # document_nodes.id
    source_page: int | None
    extract_method: Literal["keyword", "llm", "vector"]
    llm_model: NotRequired[str]
    extracted_at: str  # ISO timestamp


class ValidationResult(TypedDict):
    """标签校验结果"""

    tag_code: str
    valid: bool
    reason: str | None
    corrected_value: str | list | dict | None


class RiskItem(TypedDict):
    """风险条目"""

    risk_id: int | None
    risk_type: Literal["trap", "risk", "compet", "constraint", "reject"]
    risk_level: Literal["P0", "P1", "P2"]
    risk_title: str
    risk_desc: str
    related_tags: list[str]
    source_chunk_ids: list[str]  # document_nodes.id 列表
    suggestion: str | None
    confidence: float


class TrapScore(TypedDict):
    """萝卜坑检测评分"""

    tag_code: str
    score: float  # 0-100
    dimension_scores: dict[str, float]  # 7个维度的分项得分
    trigger_clause: str | None
    suggestion: str
    is_fatal: bool  # 是否命中致命萝卜坑条款


class BidReport(TypedDict):
    """最终报告"""

    decision: Literal["投", "不投", "待定"]
    overall_score: float  # 0-100
    qualification_score: float
    risk_score: float
    trap_score: float
    competition_score: float
    summary: str
    report_md: str
    report_json: dict


# -------------------------------------------------------------------
# 主状态机状态
# -------------------------------------------------------------------


class BidState(TypedDict):
    """LangGraph BidState - 所有节点的共享状态"""

    # === 文档基础 ===
    doc_id: int
    version_id: UUID  # DocumentVersion UUID，用于写入 document_nodes
    project_id: UUID  # 用于写入旧表 Requirement/Risk/MatchResult
    doc_name: str
    parse_status: str  # pending / parsing / done / error
    parse_error: NotRequired[str]
    raw_text: str  # 完整原始文本
    chunks: Annotated[list[ChunkInfo], _last_writer_wins]  # 文档分块

    # === L1 粗筛结果 ===
    # key: section_path, value: list of category_code
    annotations: NotRequired[dict[str, list[str]]]

    # === L2 精筛结果 ===
    # key: chunk_id (str), value: list of candidate tag_code with confidence
    candidate_tags: NotRequired[dict[str, list[tuple[str, float]]]]

    # === 提取结果（L3 精提取 + 校验） ===
    recall_tags: NotRequired[dict[str, list[str]]]  # 子图 recall 阶段结果（chunk_id 为节点 UUID）
    extracted_tags: NotRequired[dict[str, ExtractedTag]]  # key: tag_code
    validated_tags: NotRequired[dict[str, ExtractedTag]]  # 校验通过

    # === 人工复核 ===
    pending_review: NotRequired[list[str]]  # 等待复核的 tag_code 列表
    reviewed_tags: NotRequired[dict[str, ExtractedTag]]  # 人工确认后的标签
    # 以下三键此前未声明，被 LangGraph 静默丢弃导致 HITL 路由永不触发
    validation_issues: NotRequired[list[str]]
    needs_human_review: NotRequired[bool]
    review_round: NotRequired[int]

    # === Fan-out 两路并行 ===
    risk_results: NotRequired[Annotated[list[RiskItem], _merge_risk_results]]
    trap_results: NotRequired[list[TrapScore]]

    # === 企业匹配结果 ===
    enterprise_match: NotRequired[dict[str, Any]]  # match_node 返回的四维匹配结果
    enterprise_name: NotRequired[str]  # 企业名称（ENTERPRISE 材料提取/匹配用）
    enterprise_id: NotRequired[int]  # 企业画像 ep_id，精确绑定

    # === 报告 ===
    report: NotRequired[BidReport]
    trap_score: NotRequired[float]  # trap_detect 产出的总风险分（0-100）

    # === 执行追踪 ===
    thread_id: str
    current_stage: Annotated[
        Literal[
            "parse",
            "annotate",  # L1
            "tagging",  # L2
            "extract",  # L3
            "validate",
            "human_review",
            "risk_analysis",
            "trap_detect",
            "report",
            "done",
        ],
        _last_writer_wins,
    ]
    # 每个 stage 的状态：pending / running / done / error
    stage_status: Annotated[dict[str, str], _merge_stage_status]
    error_msg: NotRequired[str | None]


# -------------------------------------------------------------------
# 子图状态（extract_subgraph）
# -------------------------------------------------------------------


class ExtractSubState(TypedDict):
    """extract 子图内部状态 - recall → extract → validate"""

    doc_id: int
    version_id: UUID
    thread_id: str

    # Recall 阶段（chunk_id = document_nodes.id）
    recall_tags: NotRequired[dict[str, list[str]]]  # tag_code → chunk_ids

    # Extract 阶段（用 Annotated + reducer 支持 Send 并发写入）
    extract_tags: NotRequired[Annotated[dict[str, ExtractedTag], _merge_dict]]

    # Validate 阶段
    validation_results: NotRequired[dict[str, ValidationResult]]  # tag_code → result
    valid_tags: NotRequired[Annotated[dict[str, ExtractedTag], _merge_dict]]

    # 错误处理
    error_msg: NotRequired[str | None]


# -------------------------------------------------------------------
# 辅助类型
# -------------------------------------------------------------------


class StageInfo(TypedDict):
    """节点执行信息（用于 observability）"""

    stage: str
    node_name: str
    status: Literal["pending", "running", "done", "error"]
    started_at: str | None
    finished_at: str | None
    duration_ms: int | None
    input_summary: str | None
    output_summary: str | None
    error_msg: str | None
