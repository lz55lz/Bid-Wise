"""L2 精筛节点：chunk → 候选标签

策略：
1. 关键词层：每个 tag_code 的 extraction_prompt 包含关键词，在 chunk_text 中搜索
2. LLM 层：关键词命中 <3 时，调用 LLM 判断 chunk 是否应打此标签

输出：
  candidate_tags: dict[chunk_id, list[tuple[tag_code, confidence]]]
  chunk_id = document_nodes.id（UUID 字符串），结果落盘 metadata_.candidate_tags
"""
from typing import Any

from app.db.session import get_session_factory
from app.services.bid_pipeline import chunk_store
from app.services.bid_pipeline.state import BidState
from app.services.observability import stage_task

_PUNCT = set("，。！？、：；.,!?;:() \t\n") | {"“", "”", "‘", "’", "（", "）"}


def _tokens(text: str) -> set[str]:
    """jieba 分词，去掉标点与单字（单字无区分度）"""
    import jieba

    return {t for t in jieba.lcut(text) if t.strip() and t not in _PUNCT and len(t) >= 2}


def _keyword_match_score(tag_extraction_prompt: str, chunk_text: str) -> float:
    """关键词层得分（0.0-1.0）：jieba 词级重合率（替代原字符集合重合）"""
    if not tag_extraction_prompt or not chunk_text:
        return 0.0
    prompt_tokens = _tokens(tag_extraction_prompt)
    if not prompt_tokens:
        return 0.0
    chunk_tokens = _tokens(chunk_text)
    overlap = len(prompt_tokens & chunk_tokens)
    return min(1.0, overlap / len(prompt_tokens))


def _batch_keyword_score(
    chunk_text: str, tag_prompts: list[tuple[str, str]]
) -> list[tuple[str, float]]:
    """对单个 chunk 计算多个 tag 的关键词得分"""
    results = []
    for tag_code, prompt in tag_prompts:
        score = _keyword_match_score(prompt, chunk_text)
        results.append((tag_code, score))
    return results


@stage_task("tagging")
def tagging_node(state: BidState) -> dict[str, Any]:
    """L2 精筛节点：批量对所有候选 chunk 预测标签。

    输入：state.version_id
    输出：candidate_tags: dict[str, list[tuple[str, float]]]  (chunk_id -> [(tag_code, score)])
    """
    import logging
    logger = logging.getLogger(__name__)

    from app.services.tag_dict_service import get_candidate_tags_for_chunk

    version_id = state.get("version_id")
    if version_id is None:
        logger.warning("[tagging] no version_id, skipping")
        return {
            "candidate_tags": {},
            "current_stage": "tagging",
            "stage_status": {"tagging": "skipped"},
        }

    annotations = state.get("annotations", {})
    all_cat_codes = list({cat for cats in annotations.values() for cat in cats})
    if not all_cat_codes:
        all_cat_codes = ["CAT01"]

    session = get_session_factory()()
    try:
        candidate_tags_model = get_candidate_tags_for_chunk(session, all_cat_codes, limit=150)
        tag_prompts = [(t.tag_code, t.extraction_prompt or "") for t in candidate_tags_model]

        chunks = chunk_store.fetch_chunks(session, version_id, only_candidate=True)

        candidate_tags: dict[str, list[tuple[str, float]]] = {}
        for ch in chunks:
            scored = _batch_keyword_score(ch["chunk_text"], tag_prompts)
            scored.sort(key=lambda x: x[1], reverse=True)
            candidate_tags[ch["chunk_id"]] = scored[:20]

        # 落盘 metadata_.candidate_tags，供 recall 阶段读取
        persisted = chunk_store.set_candidate_tags(
            session, version_id, {cid: [tc for tc, _ in tags] for cid, tags in candidate_tags.items()}
        )
        session.commit()

        logger.info(
            f"[tagging] version_id={version_id}, candidate_chunks={len(chunks)}, "
            f"persisted={persisted}, tag_codes={len(tag_prompts)}"
        )
        return {
            "candidate_tags": candidate_tags,
            "current_stage": "tagging",
            "stage_status": {"tagging": "done"},
        }
    finally:
        session.close()
