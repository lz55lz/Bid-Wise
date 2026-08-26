"""clean_service — 清洗 + 关键词打分 + 章节级候选预算

整合旧 DocumentCleaningService 三层过滤：
  1. 去乱码、空格
  2. KeywordScoringService 算 keyword_score
  3. 每个一级章节按优先级保留有限候选（tender_req_candidate）

结果落盘 document_nodes.tender_req_candidate；keyword_score 等仅内存中保留，
供后续 LLM 节点使用（document_nodes 无对应列）。
"""
import json
import logging
import re
from collections import Counter, defaultdict
from typing import Any

from sqlalchemy import text

from app.db.session import get_session_factory
from app.services.bid_pipeline import chunk_store
from app.services.bid_pipeline.state import BidState
from app.services.document_text_quality import assess_text_quality, indexability_gate
from app.services.keyword_scoring_service import KeywordScoringService
from app.services.node_label_policy import NodeLabelPolicy

logger = logging.getLogger(__name__)

# MinerU can expose dozens of sub-headings under one tender chapter.  Budget
# against the primary chapter, otherwise "five per section" silently becomes
# hundreds of LLM candidates.
_PRIMARY_SECTION_LIMIT = 4
_GLOBAL_CANDIDATE_LIMIT = 48
_BUILTIN_LABEL_POLICY = NodeLabelPolicy()


def _build_section_key(section_path: str) -> str:
    """Extract the primary chapter for a stable candidate budget."""
    parts = (section_path or "").split(" / ")
    return (parts[0].strip() if parts else "")[:160] or "未归类章节"


def _candidate_priority(chunk: dict[str, Any]) -> tuple[int, int, int, int, float, int]:
    """Rank candidate clauses without making an LLM decide its own input."""
    labels = chunk["node_labels"]
    return (
        int(bool(labels.get("blocking_signal"))),
        int(bool(labels.get("mandatory_signal"))),
        int(bool(labels.get("matched_tag_codes"))),
        int(bool(labels.get("quantitative_signal"))),
        float(chunk.get("keyword_score", 0)),
        -int(chunk.get("chunk_index", 0)),
    )


def _apply_candidate_budget(chunks: list[dict[str, Any]]) -> dict[str, int]:
    """Select a bounded, chapter-balanced high-value candidate set.

    ``requirement_candidate`` remains the semantic signal.  This stage owns
    ``tender_req_candidate``, which is the scarce downstream / LLM budget.
    Every semantic candidate receives an explicit selected/deferred state for
    audit and later evaluation.
    """
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    semantic_candidates: list[dict[str, Any]] = []
    for chunk in chunks:
        labels = chunk["node_labels"]
        selected = bool(chunk.get("indexable")) and bool(
            labels.get("requirement_candidate")
        )
        chunk["tender_req_candidate"] = False
        labels["selected_candidate"] = False
        if not selected:
            continue
        semantic_candidates.append(chunk)
        groups[_build_section_key(str(chunk.get("section_path") or ""))].append(chunk)

    for candidates in groups.values():
        candidates.sort(key=_candidate_priority, reverse=True)
    ranked_groups = sorted(
        groups.items(),
        key=lambda item: _candidate_priority(item[1][0]),
        reverse=True,
    )

    selected_indexes: set[int] = set()
    for _, candidates in ranked_groups:
        for candidate in candidates[:_PRIMARY_SECTION_LIMIT]:
            if len(selected_indexes) >= _GLOBAL_CANDIDATE_LIMIT:
                break
            selected_indexes.add(int(candidate["chunk_index"]))
        if len(selected_indexes) >= _GLOBAL_CANDIDATE_LIMIT:
            break

    deferred_blocking = 0
    for candidate in semantic_candidates:
        selected = int(candidate["chunk_index"]) in selected_indexes
        candidate["tender_req_candidate"] = selected
        labels = candidate["node_labels"]
        labels["selected_candidate"] = selected
        labels["selection_reason"] = (
            "PRIMARY_SECTION_BUDGET" if selected else "DEFERRED_BY_SECTION_BUDGET"
        )
        if not selected and candidate["node_labels"].get("blocking_signal"):
            deferred_blocking += 1
    return {
        "semantic_candidates": len(semantic_candidates),
        "selected_candidates": len(selected_indexes),
        "deferred_candidates": len(semantic_candidates) - len(selected_indexes),
        "primary_sections": len(groups),
        "deferred_blocking_candidates": deferred_blocking,
    }


def _label_node(
    chunk: dict[str, Any], policy: NodeLabelPolicy | None = None
) -> dict[str, Any]:
    """Apply the maintained tag policy, or the deterministic bootstrap fallback."""
    return (policy or _BUILTIN_LABEL_POLICY).label(chunk)


def _load_label_policy(version_id: Any) -> NodeLabelPolicy:
    """Read the active tag library when this is a persisted document run."""
    if version_id is None:
        return _BUILTIN_LABEL_POLICY
    try:
        session = get_session_factory()()
    except Exception:
        logger.warning("[clean] 标签库连接不可用，使用内置基线")
        return _BUILTIN_LABEL_POLICY
    try:
        return NodeLabelPolicy.from_session(session)
    finally:
        session.close()


async def score_and_clean_chunks(
    chunks: list[dict], doc_id: int, version_id: Any = None
) -> dict[str, Any]:
    """对 chunks 列表执行 清洗 + 打分 + 候选限流，结果写回 DB。

    Args:
        chunks: list[dict]，每个 dict 包含 chunk_text, section_path, chunk_type, chunk_id
        doc_id: 旧链路文档 ID（仅日志用）
        version_id: DocumentVersion UUID，落盘 tender_req_candidate

    Returns:
        {"chunks": chunks, "current_stage": "clean"}
        chunks 被原地修改（加了标签、质量闸门、候选预算和关键词分数）
    """
    # 1. 去乱码
    for ch in chunks:
        text_content = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", ch["chunk_text"])
        text_content = re.sub(r"\s+", " ", text_content).strip()
        ch["chunk_text"] = text_content
        text_quality = assess_text_quality(text_content)
        ch["text_quality"] = text_quality
        quality_gate = indexability_gate(text_content, text_quality)
        ch["quality_gate"] = quality_gate
        # indexable 是索引和后续 Agent 的共同入口。乱码、目录或未解析的大块
        # 文本不应只被标记后继续喂给标签器、抽取器或向量索引。
        ch["indexable"] = quality_gate is None

    # 2. 关键词打分
    scorer = KeywordScoringService()
    label_policy = _load_label_policy(version_id)
    for ch in chunks:
        score, matched = scorer.score_node(ch["chunk_text"], ch.get("section_path", ""))
        ch["keyword_score"] = score
        ch["keyword_matched"] = matched
        labels = _label_node(ch, label_policy)
        if quality_gate := ch["quality_gate"]:
            labels = {
                **labels,
                "noise": True,
                "requirement_candidate": False,
                "quality_gate": quality_gate,
            }
        ch["node_labels"] = labels

    # 3. Chapter-balanced candidate budget.  Titles/images are already
    # excluded by the label policy; a qualifying table/list still competes in
    # the same finite budget rather than bypassing it.
    candidate_summary = _apply_candidate_budget(chunks)

    # 4. 落盘清洗结果与候选标记：
    #    cleaned_content + cleaning_metadata.indexable 是 DocumentIndexingService
    #    构建问答索引的输入条件，不落盘则 do_index 视为"无可索引节点"
    updated = 0
    if version_id is not None:
        session = get_session_factory()()
        try:
            stmt = text("""
                UPDATE app.document_nodes
                SET cleaned_content = :content,
                    cleaning_metadata = COALESCE(cleaning_metadata, '{}'::jsonb)
                        || CAST(:labels AS jsonb)
                WHERE document_version_id = :version_id AND order_no = :order_no
            """)
            session.execute(
                stmt,
                [
                    {
                        # The parsed source is stored in a TEXT column.  The
                        # quality gate may reject an oversized unresolved node,
                        # but clean must not create a silently shortened copy.
                        "content": ch["chunk_text"],
                        "labels": json.dumps({
                            "keyword_score": ch.get("keyword_score", 0),
                            "keyword_matched": ch.get("keyword_matched", []),
                            "node_labels": ch.get("node_labels", {}),
                            "indexable": ch.get("indexable", False),
                            "garbled_ratio": round(
                                ch["text_quality"].garbled_ratio, 4
                            ),
                            "garbled_characters": ch["text_quality"].garbled_characters,
                            "unexpected_script_characters": (
                                ch["text_quality"].unexpected_script_characters
                            ),
                        }),
                        "version_id": str(version_id),
                        "order_no": ch["chunk_index"],
                    }
                    for ch in chunks
                    if ch.get("chunk_text")
                ],
            )
            candidate_indexes = [
                ch["chunk_index"] for ch in chunks if ch.get("tender_req_candidate")
            ]
            updated = chunk_store.set_tender_candidates(session, version_id, candidate_indexes)
            session.execute(
                text("""
                    UPDATE app.document_versions
                    SET cleaning_summary = COALESCE(cleaning_summary, '{}'::jsonb)
                        || CAST(:label_policy AS jsonb)
                    WHERE id = :version_id
                """),
                {
                    "version_id": str(version_id),
                    "label_policy": json.dumps({
                        "node_label_policy": label_policy.summary(),
                        "cleaning_quality": {
                            "total_nodes": len(chunks),
                            "indexable_nodes": sum(
                                bool(ch.get("indexable")) for ch in chunks
                            ),
                            "rejected_by_gate": dict(
                                Counter(
                                    ch["quality_gate"]
                                    for ch in chunks
                                    if ch.get("quality_gate")
                                )
                            ),
                        },
                        "candidate_selection": candidate_summary,
                    }),
                },
            )
            session.commit()
        finally:
            session.close()

    tender_cnt = sum(1 for ch in chunks if ch.get("tender_req_candidate"))
    rejected = Counter(
        ch["quality_gate"] for ch in chunks if ch.get("quality_gate")
    )
    logger.info(
        f"[clean] doc_id={doc_id}, chunks={len(chunks)}, "
        f"persisted_candidates={updated}, tender_candidates={tender_cnt}, "
        f"quality_rejected={dict(rejected)}, "
        f"candidate_selection={candidate_summary}, "
        f"label_policy={label_policy.version[:12]}"
    )

    return {"chunks": chunks, "current_stage": "clean"}


async def clean_node(state: BidState) -> dict[str, Any]:
    """清洗节点入口（graph.py 调用）"""
    return await score_and_clean_chunks(
        state.get("chunks", []), state["doc_id"], state.get("version_id")
    )
