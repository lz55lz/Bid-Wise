"""extract_subgraph: recall -> extract -> validate

召回策略（全部基于 document_nodes，无向量层——bid_doc_chunk.embedding 从不写入）：
  1. candidate_tags 命中的节点做 BM25 全文检索（zhparser）
  2. 未命中时对全量节点做关键词密度扫描
  3. P0 tag 兜底使用文档头部节点

提取策略：
  Layer1: 按 8 tags/batch 批量 JSON-mode 提取
  Layer2: 缺失/低置信 tag 的二次批量提取
  Layer3: 跨标签一致性交叉验证
  Fallback: 日期类 regex 兜底 + 结构化值 regex/关键词兜底

结果写入 bid_document_tag（键 version_id + tag_id），模型标识固定 deepseekv4。
"""
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph
from sqlalchemy import text

from app.core.config import get_settings
from app.core.constants import LLM_MODEL_ID as BID_LLM_MODEL
from app.db.session import get_session_factory
from app.services.bid_pipeline import chunk_store
from app.services.bid_pipeline.state import BidState, ExtractSubState

logger = logging.getLogger(__name__)

TAGS_PER_BATCH = 8
MAX_CHUNK_CHARS = 1500


# ---------------------------------------------------------------------------
# LLM client（模块级单例）
# ---------------------------------------------------------------------------

_llm_client: Any = None


def _get_json_llm() -> Any:
    global _llm_client
    if _llm_client is None:
        from langchain_openai import ChatOpenAI

        settings = get_settings()
        _llm_client = ChatOpenAI(
            model=settings.llm_model_name,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
            base_url=settings.llm_base_url,
            temperature=0.0,
            extra_body={"thinking": {"type": "disabled"}},
        )
    return _llm_client


# ---------------------------------------------------------------------------
# 1. recall
# ---------------------------------------------------------------------------

def _extract_keywords(extraction_prompt: str) -> list[str]:
    quoted = re.findall(r'"([^"]{2,})"', extraction_prompt)
    if quoted:
        return quoted
    chinese = re.findall(r"[一-鿿]{2,}", extraction_prompt)
    if chinese:
        return [w for w in chinese if len(w) >= 2]
    return [extraction_prompt[:40].strip()]


def _keyword_score(extraction_prompt: str, chunk_text: str) -> float:
    """关键词密度得分（0.0-1.0），无索引时的通用兜底。"""
    if not extraction_prompt or not chunk_text:
        return 0.0
    kws = _extract_keywords(extraction_prompt)
    chunk_lower = chunk_text.lower()
    hit = sum(1 for kw in kws if kw.lower() in chunk_lower)
    return min(hit / max(len(kws), 1), 1.0)


def _bm25_search(session, chunk_ids: list[str], keywords: list[str], limit: int = 20) -> list[tuple[str, float]]:
    """PG 全文检索（zhparser 'zh' config），失败时退化为 ILIKE。"""
    if not chunk_ids or not keywords:
        return []
    joined_query = " OR ".join(k.strip() for k in keywords if k and k.strip())
    if not joined_query:
        return []
    try:
        result = session.execute(
            text("""
                SELECT c.id::text,
                       ts_rank_cd(to_tsvector('zh', c.content), query) AS rank_score
                FROM app.document_nodes c,
                     websearch_to_tsquery('zh', :kw_query) query
                WHERE c.id = ANY(:ids) AND to_tsvector('zh', c.content) @@ query
                ORDER BY rank_score DESC
                LIMIT :limit
            """),
            {"kw_query": joined_query, "ids": chunk_ids, "limit": limit},
        )
        return [(row[0], float(row[1])) for row in result.fetchall()]
    except Exception as exc:
        logger.debug(f"[recall] BM25 unavailable: {exc}")
    like_pattern = "%" + "%".join(keywords) + "%"
    result = session.execute(
        text("""
            SELECT id::text, 1.0 FROM app.document_nodes
            WHERE id = ANY(:ids) AND content ILIKE :pattern
            ORDER BY order_no LIMIT :limit
        """),
        {"ids": chunk_ids, "pattern": like_pattern, "limit": limit},
    )
    return [(row[0], 1.0) for row in result.fetchall()]


def _search_bm25_by_keyword(
    session, chunk_ids: list[str], keywords: list[str], limit: int = 20
) -> list[tuple[str, float]]:
    """Focused, testable boundary for the Chinese BM25 recall query."""
    return _bm25_search(session, chunk_ids, keywords, limit)


def recall_node(state: ExtractSubState) -> dict[str, Any]:
    """Recall: candidate BM25 + 全量关键词扫描 + P0 默认头部。"""
    import time

    t0 = time.time()
    version_id = state.get("version_id")
    if version_id is None:
        logger.warning("[recall] no version_id, nothing to recall")
        return {"recall_tags": {}}

    session = get_session_factory()()
    try:
        tag_infos = session.execute(
            text("SELECT tag_code, extraction_prompt, level_code FROM bid_tag_dict "
                 "WHERE level_code IN ('P0','P1') AND is_active = true")
        ).fetchall()
        chunks = chunk_store.fetch_chunks(session, version_id)
    finally:
        session.close()

    all_chunk_ids = [c["chunk_id"] for c in chunks]
    if not all_chunk_ids:
        logger.warning(f"[recall] version={version_id}: no chunks")
        return {"recall_tags": {}}

    all_chunk_texts = {c["chunk_id"]: c["chunk_text"] for c in chunks}
    tag_meta = {r[0]: (r[1] or "", r[2]) for r in tag_infos}
    p1_codes = {code for code, (_, level) in tag_meta.items() if level == "P1"}

    # candidate_tags 反向映射（tag -> 候选节点集）
    tag_to_candidates: dict[str, set[str]] = {tc: set() for tc in tag_meta}
    for ch in chunks:
        for tc in ch["candidate_tags"]:
            if tc in tag_to_candidates:
                tag_to_candidates[tc].add(ch["chunk_id"])

    p0_default_ids = all_chunk_ids[:30]
    recall_tags: dict[str, list[str]] = {}

    for i, (tag_code, (extraction_prompt, _level)) in enumerate(tag_meta.items()):
        if i % 10 == 0:
            logger.info(f"[recall] progress: {i}/{len(tag_meta)} tags")
        keywords = _extract_keywords(extraction_prompt)
        candidate_ids = list(tag_to_candidates.get(tag_code, ()))

        hits: list[tuple[str, float]] = []
        if candidate_ids:
            session = get_session_factory()()
            try:
                hits = _search_bm25_by_keyword(session, candidate_ids, keywords, limit=40)
            finally:
                session.close()

        if not hits:
            # 全量关键词密度扫描
            scored = [
                (cid, _keyword_score(extraction_prompt, txt))
                for cid, txt in all_chunk_texts.items()
            ]
            hits = [(cid, s) for cid, s in scored if s > 0]

        if hits:
            hits.sort(key=lambda x: x[1], reverse=True)
            recall_tags[tag_code] = [cid for cid, _ in hits[:12]]
        elif tag_code not in p1_codes:
            # P0 兜底：文档头部
            recall_tags[tag_code] = p0_default_ids[:10]

    recalled = sum(1 for ids in recall_tags.values() if ids)
    logger.info(
        f"[recall] version={version_id} chunks={len(all_chunk_ids)} "
        f"tags={len(tag_meta)} recalled={recalled} elapsed={time.time()-t0:.1f}s"
    )
    return {"recall_tags": recall_tags}


# ---------------------------------------------------------------------------
# 2. fallback helpers
# ---------------------------------------------------------------------------

DATE_TAG_CODES = {
    "BID_DEADLINE", "OPEN_BID_TIME", "PROJECT_DURATION", "BID_VALIDITY",
    "REGISTER_DEADLINE", "QUESTION_DEADLINE",
}
CONCRETE_DATE_RE = re.compile(
    r"\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[T\s]\d{1,2}:\d{1,2})?"
    r"|\d{4}年\d{1,2}月\d{1,2}[日号]?(?:[日号]?\s*\d{1,2}时?\d{0,2}分?)?"
    r"|\d{1,2}月\d{1,2}[日号]?\s*\d{1,2}时\d{0,2}分?"
    r"|\d{4}\s*年\s*\d{1,2}\s*月"
    r"|(?:至|~|—|-)\d{4}[-/]\d{1,2}[-/]\d{1,2}"
)

TAG_PATTERNS: dict[str, tuple[str | list[str], str]] = {
    "PROJECT_CODE": (r"[A-Z]{2,}\d{8,}", "str"),
    "PROJECT_BUDGET": (r"[万元元：]?\s*\d+(?:\.\d+)?\s*(?:万元|元|万)", "float"),
    "PRICE_SCORE_RATIO": (r"\d+(?:\.\d+)?%", "float"),
    "TECH_SCORE_RATIO": (r"\d+(?:\.\d+)?%", "float"),
    "QUAL_ISO_CERT": (r"ISO\s*\d{4}", "str"),
    "RISK_UNFAIR": ([r"不公平", r"不合理", r"显失公平"], "str"),
    "RISK_EXCLUSIVE": ([r"唯一", r"排他", r"限定", r"指定"], "str"),
    "TECH_REQUIREMENT": ([r"技术", r"规格", r"要求"], "str"),
    "REJECT_QUAL_MISSING": ([r"资格", r"资质", r"不符", r"缺失"], "str"),
    "TENDERER_NAME": ([r"招标人", r"招标单位", r"发包人", r"项目法人", r"建设单位"], "str"),
    "PROJECT_LOCATION": ([r"建设地点", r"项目地点", r"工程地址", r"位于"], "str"),
}


def _load_chunk_texts(version_id) -> dict[str, str]:
    session = get_session_factory()()
    try:
        return {c["chunk_id"]: c["chunk_text"] for c in chunk_store.fetch_chunks(session, version_id)}
    finally:
        session.close()


def _date_fallback(chunk_texts: dict[str, str], recall_tags: dict, all_tag_codes: list[str], extract_tags: dict) -> None:
    """日期类 tag 的 regex 兜底：未提取到具体日期时扫描召回/全量节点。"""
    for tag_code in all_tag_codes:
        if tag_code not in DATE_TAG_CODES:
            continue
        td = extract_tags.get(tag_code)
        if not td:
            td = {"tag_code": tag_code, "tag_value": None, "confidence": 0.0,
                  "source_text": "", "extract_method": "llm"}
            extract_tags[tag_code] = td
        val = str(td.get("tag_value") or "")
        if CONCRETE_DATE_RE.search(val):
            continue
        search_ids = recall_tags.get(tag_code, []) or list(chunk_texts.keys())
        for cid in search_ids:
            text_content = chunk_texts.get(cid, "")
            m = CONCRETE_DATE_RE.search(text_content)
            if m:
                td["tag_value"] = m.group()
                td["confidence"] = max(td.get("confidence", 0) or 0, 0.6)
                td["source_text"] = text_content[max(0, m.start() - 50):m.end() + 50]
                td["extract_method"] = "regex_fallback"
                td["source_chunk_id"] = cid
                break


def _structured_value_fallback(chunk_texts: dict[str, str], recall_tags: dict, all_tag_codes: list[str], extract_tags: dict) -> None:
    """低置信 str/list/float tag 的内容模式兜底。"""
    for tag_code in all_tag_codes:
        td = extract_tags.get(tag_code)
        conf = (td.get("confidence", 0) or 0) if td else 0
        if conf >= 0.7 or tag_code not in TAG_PATTERNS:
            continue
        pattern, _expected_type = TAG_PATTERNS[tag_code]
        search_ids = recall_tags.get(tag_code, []) or list(chunk_texts.keys())

        if isinstance(pattern, list):
            for cid in search_ids:
                text_content = chunk_texts.get(cid, "")
                if any(kw in text_content for kw in pattern):
                    td = td or {"tag_code": tag_code, "confidence": 0.0, "source_text": "",
                                "extract_method": "keyword_fallback"}
                    td.update({
                        "tag_value": text_content[:200],
                        "confidence": max(conf, 0.5),
                        "source_text": text_content[:200],
                        "extract_method": "keyword_fallback",
                        "source_chunk_id": cid,
                    })
                    extract_tags[tag_code] = td
                    break
        else:
            rx = re.compile(pattern)
            for cid in search_ids:
                text_content = chunk_texts.get(cid, "")
                m = rx.search(text_content)
                if m:
                    td = td or {"tag_code": tag_code, "confidence": 0.0, "source_text": "",
                                "extract_method": "regex_fallback"}
                    td.update({
                        "tag_value": m.group(),
                        "confidence": max(conf, 0.5),
                        "source_text": text_content[max(0, m.start() - 50):m.end() + 50],
                        "extract_method": "regex_fallback",
                        "source_chunk_id": cid,
                    })
                    extract_tags[tag_code] = td
                    break


# ---------------------------------------------------------------------------
# 3. extract
# ---------------------------------------------------------------------------

def _parse_results(raw_content: str) -> list[dict]:
    """Parse LLM JSON output: handles markdown fences, partial JSON, nested braces."""
    import re as re_mod
    if not isinstance(raw_content, str):
        return []
    t = re_mod.sub(r"^```(json)?\s*", "", raw_content.strip())
    t = re_mod.sub(r"```\s*$", "", t).strip()

    def try_parse(value: str):
        value = value.strip()
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass
        depth, start = 0, -1
        for i, ch in enumerate(value):
            if ch == "{" and start == -1:
                start = i
            if start != -1:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        cand = value[start:i + 1]
                        for attempt in (cand, cand.replace("'", '"'), cand.replace('\n', ' ')):
                            try:
                                return json.loads(attempt)
                            except json.JSONDecodeError:
                                pass
                        break
        return None

    parsed = try_parse(t)
    if not parsed:
        return []
    if isinstance(parsed, dict) and "results" in parsed:
        return parsed.get("results", [])
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    return []


def _fetch_all_tag_infos(tag_codes: list[str]) -> list[dict[str, str]]:
    if not tag_codes:
        return []
    session = get_session_factory()()
    try:
        result = session.execute(
            text("""
                SELECT tag_code, tag_name, extraction_prompt, data_type, value_example
                FROM bid_tag_dict WHERE tag_code = ANY(:codes)
            """),
            {"codes": tag_codes},
        )
        return [
            {"tag_code": r[0], "tag_name": r[1] or "", "extraction_prompt": r[2] or "",
             "data_type": r[3] or "string", "value_example": r[4] or ""}
            for r in result.fetchall()
        ]
    finally:
        session.close()


def _build_context(chunk_ids: list[str]) -> tuple[str, list[dict], dict[int, str]]:
    """按节点 UUID 拉取上下文文本，按文档顺序拼接。

    返回 (拼接文本, chunks, {chunk_index: chunk_id})——LLM 按上下文里的
    ChunkN 标号回指证据节点，凭 chunk_index 映射回真实节点 UUID。
    """
    if not chunk_ids:
        return "", [], {}
    session = get_session_factory()()
    try:
        chunks = chunk_store.fetch_chunks_by_ids(session, chunk_ids)
    finally:
        session.close()
    texts = [
        f"[{c['section_path']}] Chunk{c['chunk_index']}:\n{c['chunk_text'][:MAX_CHUNK_CHARS]}"
        if c["section_path"] else f"Chunk{c['chunk_index']}:\n{c['chunk_text'][:MAX_CHUNK_CHARS]}"
        for c in chunks if c["chunk_text"]
    ]
    cid_map = {c["chunk_index"]: c["chunk_id"] for c in chunks if c["chunk_text"]}
    return "\n\n".join(texts), chunks, cid_map


def _evidence_chunk_id(item: dict, cid_map: dict[int, str], recall_ids: list[str]) -> str:
    """把 LLM 回指的 ChunkN 标号映射回节点 UUID；LLM 未回指时退回召回首位。"""
    try:
        idx = int(item.get("chunk_index"))
    except (TypeError, ValueError):
        idx = None
    if idx is not None and idx in cid_map:
        return cid_map[idx]
    return recall_ids[0] if recall_ids else ""


def _save_extracted_tags_to_db(version_id, extract_tags: dict[str, Any]) -> None:
    """把提取结果写入 bid_document_tag（键 version_id + tag_id）。"""
    if not extract_tags:
        return
    session = get_session_factory()()
    try:
        rows = session.execute(
            text("SELECT tag_id, tag_code FROM bid_tag_dict WHERE tag_code = ANY(:codes)"),
            {"codes": list(extract_tags.keys())},
        ).fetchall()
        tag_id_map = {code: tid for tid, code in rows}

        for tag_code, td in extract_tags.items():
            tag_id = tag_id_map.get(tag_code)
            if not tag_id:
                continue
            tag_value = td.get("tag_value")
            tag_value_json = None
            if isinstance(tag_value, (dict, list)):
                tag_value_json = tag_value
                tag_value = None
            session.execute(
                text("""
                    INSERT INTO bid_document_tag
                    (version_id, tag_id, tag_value, tag_value_json, source_text, source_node_id,
                     source_page, confidence, extract_method, llm_model, extracted_at, reviewed)
                    VALUES (:version_id, :tag_id, :tag_value, CAST(:tag_value_json AS jsonb),
                            :source_text, :source_node_id, :source_page, :confidence,
                            :extract_method, :llm_model, :extracted_at, false)
                    ON CONFLICT (version_id, tag_id) DO UPDATE SET
                        tag_value = EXCLUDED.tag_value,
                        tag_value_json = EXCLUDED.tag_value_json,
                        source_text = EXCLUDED.source_text,
                        source_node_id = EXCLUDED.source_node_id,
                        confidence = EXCLUDED.confidence,
                        extract_method = EXCLUDED.extract_method,
                        llm_model = EXCLUDED.llm_model,
                        extracted_at = EXCLUDED.extracted_at
                """),
                {
                    "version_id": str(version_id),
                    "tag_id": tag_id,
                    "tag_value": str(tag_value) if tag_value is not None and not isinstance(tag_value, (dict, list)) else tag_value,
                    "tag_value_json": json.dumps(tag_value_json, ensure_ascii=False) if tag_value_json is not None else None,
                    "source_text": (td.get("source_text") or "")[:500],
                    "source_node_id": td.get("source_chunk_id"),
                    "source_page": td.get("source_page"),
                    "confidence": float(td.get("confidence", 0.0) or 0.0),
                    "extract_method": td.get("extract_method", "llm"),
                    "llm_model": BID_LLM_MODEL,
                    "extracted_at": datetime.now(UTC),
                },
            )
        session.commit()
        logger.info(f"[extract] saved {len(extract_tags)} tags, version={version_id}")
    except Exception as e:
        logger.warning(f"[extract] failed to save tags: {e}")
        session.rollback()
    finally:
        session.close()


def _extract_batch_tags(state: ExtractSubState) -> dict[str, Any]:
    """Layer1 批量提取 + Layer2 补漏 + Layer3 交叉验证 + fallback + 落盘。"""
    version_id = state.get("version_id")
    recall_tags: dict[str, list[str]] = state.get("recall_tags", {})
    if not recall_tags or version_id is None:
        return {"extract_tags": {}}

    tag_codes = list(recall_tags.keys())
    tag_infos = _fetch_all_tag_infos(tag_codes)
    if not tag_infos:
        return {"extract_tags": {}}
    ti_map = {ti["tag_code"]: ti for ti in tag_infos}
    llm = _get_json_llm()

    session = get_session_factory()()
    try:
        all_p0p1_codes = [
            r[0] for r in session.execute(
                text("SELECT tag_code FROM bid_tag_dict WHERE level_code IN ('P0','P1') AND is_active = true")
            ).fetchall()
        ]
    finally:
        session.close()

    extract_tags: dict[str, Any] = {}

    # ── Layer 1: 批量 JSON-mode 提取 ──
    # 每 tag 带 top12 召回节点、批上限 200（实测收缩到 5/80、8/120 都会让提取值
    # 显示减少——对长招标文件，上下文覆盖比防稀释更重要），批间线程池并发
    def _invoke_layer1_batch(batch_idx: int, tc_batch: list[str]) -> tuple[list[dict], dict[int, str]]:
        ids_used: list[str] = []
        for tc in tc_batch:
            ids_used.extend(recall_tags.get(tc, [])[:12])
        unique_ids = list(dict.fromkeys(ids_used))[:200]
        batch_context, _, cid_map = _build_context(unique_ids)

        batch_tag_str = "\n".join(
            f"- {ti_map[tc]['tag_code']} ({ti_map[tc]['tag_name']}): {ti_map[tc]['extraction_prompt']} | Example: {ti_map[tc]['value_example']}"
            for tc in tc_batch if tc in ti_map
        )
        batch_prompt = (
            "You are an expert at extracting structured information from bid documents.\n"
            "Tag list:\n" + batch_tag_str + "\nRequirements:\n"
            "1. Strictly follow the extraction_prompt instructions for the search scope\n"
            "2. Only extract when there is clear evidence, do not guess\n"
            "3. Use the most concise expression for tag_value (numbers only keep digits, text only keep keywords)\n"
            "4. Return a JSON array, each element contains tag_code, tag_value, confidence, reason\n"
            "5. Each element must include chunk_index: the N from the \"ChunkN\" label where "
            "the evidence was found (do not guess, use the label shown in the document)\n"
            "Return format:\n"
            '[{"tag_code": "PROJECT_NAME", "tag_value": "XX Project", "confidence": 0.95, '
            '"reason": "Explicit in title", "chunk_index": 2}]\n'
            "Only return tags with clear evidence, skip tags that cannot be extracted."
        )

        for attempt in range(3):
            try:
                resp = llm.invoke([
                    HumanMessage(content=batch_prompt),
                    HumanMessage(
                        content=f"Document fragment ({batch_idx + 1}/{len(batches)}):\n{batch_context}"
                        if batch_context else "No relevant document context for this batch."
                    ),
                ])
                raw = resp.content if hasattr(resp, "content") else str(resp)
                return _parse_results(raw), cid_map
            except Exception as e:
                retryable = any(
                    code in str(e)
                    for code in ("429", "529", "500", "502", "503", "timed out", "overloaded", "rate limit")
                )
                if retryable and attempt < 2:
                    import time
                    time.sleep((attempt + 1) * 3)
                    continue
                logger.warning(f"[extract] batch {batch_idx + 1} failed: {e}")
                return [], {}

    batches = [tag_codes[i:i + TAGS_PER_BATCH] for i in range(0, len(tag_codes), TAGS_PER_BATCH)]
    with ThreadPoolExecutor(max_workers=3) as pool:
        batch_results = [
            f.result() for f in [
                pool.submit(_invoke_layer1_batch, i, b) for i, b in enumerate(batches)
            ]
        ]
    for items, cid_map in batch_results:
        for item in items:
            tc = item.get("tag_code")
            if tc and tc in ti_map and tc not in extract_tags:
                extract_tags[tc] = {
                    "tag_code": tc,
                    "tag_name": ti_map[tc]["tag_name"],
                    "tag_value": item.get("tag_value"),
                    "confidence": item.get("confidence", 0.0),
                    "source_text": (item.get("reason") or "")[:200],
                    "source_chunk_id": _evidence_chunk_id(item, cid_map, recall_tags.get(tc, [])),
                    "source_page": None,
                    "extract_method": "llm",
                    "llm_model": BID_LLM_MODEL,
                    "extracted_at": datetime.now(UTC).isoformat(),
                }

    # ── Layer 2: 缺失/低置信 tag 二次批量提取 ──
    step2_codes = {
        ti["tag_code"] for ti in tag_infos if ti["tag_code"] not in extract_tags
    } | {
        tc for tc, td in extract_tags.items() if (td.get("confidence", 0) or 0) < 0.7
    }
    if step2_codes:
        logger.info(f"[extract] Layer2 for {len(step2_codes)} tags")
        step2_tags = list(step2_codes)
        for batch_start in range(0, len(step2_tags), 4):
            batch_tags = step2_tags[batch_start:batch_start + 4]
            batch_chunk_ids: list[str] = []
            for tc in batch_tags:
                batch_chunk_ids.extend(recall_tags.get(tc, [])[:10])
            batch_chunk_ids = list(dict.fromkeys(batch_chunk_ids))[:50]
            batch_context, _, cid_map = _build_context(batch_chunk_ids)

            parts = []
            for tc in batch_tags:
                ti = ti_map.get(tc, {})
                parts.append(
                    f'- {tc}: instruction="{ti.get("extraction_prompt", "")}" '
                    f'example="{ti.get("value_example", "")}"'
                )
            prompt = (
                "You are a precise information extraction assistant. For each tag below, "
                "read the relevant document chunks and extract the tag value.\n"
                "Return a JSON array with one object per tag:\n"
                '[{"tag_code": "TC001", "tag_value": "...", "confidence": 0.9, "reason": "...", '
                '"chunk_index": 3}]\n'
                "chunk_index is the N from the \"ChunkN\" label where the evidence was found.\n"
                "Only return tags with clear evidence. Tags:\n" + "\n".join(parts)
            )
            try:
                resp = _get_json_llm().invoke([
                    HumanMessage(content=prompt),
                    HumanMessage(content=f"Document:\n{batch_context}"),
                ])
                raw = resp.content if hasattr(resp, "content") else str(resp)
                for item in _parse_results(raw):
                    tc = item.get("tag_code")
                    if tc not in step2_codes or item.get("tag_value") is None:
                        continue
                    existing = extract_tags.get(tc)
                    # 允许二次结果修正：补全缺失，或以更高置信覆盖一次低置信值
                    if existing is not None and (existing.get("tag_value") is not None) \
                            and float(item.get("confidence") or 0) <= (existing.get("confidence") or 0):
                        continue
                    extract_tags[tc] = {
                        "tag_code": tc,
                        "tag_name": ti_map.get(tc, {}).get("tag_name", ""),
                        "tag_value": item.get("tag_value"),
                        "confidence": item.get("confidence", 0.5),
                        "source_text": (item.get("reason") or "")[:200],
                        "source_chunk_id": _evidence_chunk_id(item, cid_map, recall_tags.get(tc, [])),
                        "source_page": None,
                        "extract_method": "llm_tool",
                        "llm_model": BID_LLM_MODEL,
                        "extracted_at": datetime.now(UTC).isoformat(),
                    }
            except Exception as e:
                logger.warning(f"[extract] Layer2 batch failed: {e}")

    # ── Layer 3: 交叉验证（一致性降分） ──
    extracted_for_validation = {
        tc: td for tc, td in extract_tags.items() if td.get("tag_value") is not None
    }
    if len(extracted_for_validation) >= 2:
        try:
            cross_prompt = (
                "You are a consistency validator for bid document extraction.\n"
                "Below are extracted tags with their values and confidence scores.\n"
                + json.dumps(
                    {tc: {"value": td.get("tag_value"), "conf": td.get("confidence", 0)}
                     for tc, td in extracted_for_validation.items()},
                    ensure_ascii=False, indent=2,
                )
                + "\nCheck consistency between related tags. Return a JSON object:\n"
                '{"inconsistencies": [{"tag_a": "...", "tag_b": "...", "value_a": "...", "value_b": "...", '
                '"issue": "...", "suggested_conf_a": 0.5, "suggested_conf_b": 0.5}]}\n'
                'If everything is consistent, return {"inconsistencies": []}.'
            )
            cross_resp = _get_json_llm().invoke([HumanMessage(content=cross_prompt)])
            cross_raw = cross_resp.content if hasattr(cross_resp, "content") else str(cross_resp)
            cross_data = _parse_results(cross_raw)
            if cross_data:
                for inc in cross_data[0].get("inconsistencies", []):
                    ta, tb = inc.get("tag_a", ""), inc.get("tag_b", "")
                    for tag_key, conf_key in ((ta, "suggested_conf_a"), (tb, "suggested_conf_b")):
                        conf_val = inc.get(conf_key)
                        if tag_key in extract_tags and conf_val is not None:
                            extract_tags[tag_key]["confidence"] = float(conf_val)
                            extract_tags[tag_key]["source_text"] = (
                                extract_tags[tag_key].get("source_text") or ""
                            ) + f" [交叉验证降分: {inc.get('issue', '')}]"
        except Exception as e:
            logger.warning(f"[extract] Layer3 error: {e}")

    # 未提取到的 tag 补空记录
    for ti in tag_infos:
        if ti["tag_code"] not in extract_tags:
            recall_ids = recall_tags.get(ti["tag_code"], [])
            extract_tags[ti["tag_code"]] = {
                "tag_code": ti["tag_code"],
                "tag_name": ti["tag_name"],
                "tag_value": None,
                "confidence": 0.0,
                "source_text": "",
                "source_chunk_id": recall_ids[0] if recall_ids else "",
                "source_page": None,
                "extract_method": "none",
                "extracted_at": datetime.now(UTC).isoformat(),
            }

    # Fallback：日期 + 结构化值（共享一次全量节点文本加载）
    _chunk_texts = _load_chunk_texts(version_id)
    _date_fallback(_chunk_texts, recall_tags, all_p0p1_codes, extract_tags)
    _structured_value_fallback(_chunk_texts, recall_tags, all_p0p1_codes, extract_tags)

    # 落盘（唯一写入口）
    _save_extracted_tags_to_db(version_id, extract_tags)

    return {"extract_tags": extract_tags}


# ---------------------------------------------------------------------------
# 4. validate
# ---------------------------------------------------------------------------

def _validate_single_tag(tag_code: str, tag_value: Any, tag_infos: list[dict]) -> dict[str, Any]:
    ti = next((t for t in tag_infos if t["tag_code"] == tag_code), {})
    if not ti:
        return {"valid": False, "reason": "Tag not found", "corrected_value": None}
    if tag_value is None or tag_value == "":
        return {"valid": False, "reason": "Extracted value is empty", "corrected_value": None}
    return {"valid": True, "reason": "Valid extraction", "corrected_value": tag_value}


async def validate_node(state: Any) -> dict[str, Any]:
    extracted_tags = state.get("extract_tags", {})
    if not extracted_tags:
        return {"validated_tags": {}, "current_stage": "validate", "stage_status": {"validate": "done"}}
    tag_infos = _fetch_all_tag_infos(list(extracted_tags.keys()))
    validated = {}
    for tag_code, tag_data in extracted_tags.items():
        result = _validate_single_tag(tag_code, tag_data.get("tag_value"), tag_infos)
        validated[tag_code] = {**tag_data, **result}
    return {"validated_tags": validated, "current_stage": "validate", "stage_status": {"validate": "done"}}


def build_extract_subgraph() -> StateGraph:
    """Build extract_subgraph: recall -> extract -> validate"""
    from app.services.bid_pipeline.state import ExtractSubState
    builder = StateGraph(ExtractSubState)
    builder.add_node("recall", recall_node)
    builder.add_node("extract", _extract_batch_tags)
    builder.add_node("validate", validate_node)
    builder.add_edge("recall", "extract")
    builder.add_edge("extract", "validate")
    builder.set_entry_point("recall")
    builder.set_finish_point("validate")
    return builder


# ---------------------------------------------------------------------------
# 5. Main node entry（BidState 调用子图）
# ---------------------------------------------------------------------------

async def extract_node(state: BidState) -> dict[str, Any]:
    sub_state: ExtractSubState = {
        "doc_id": state.get("doc_id", 0),
        "version_id": state.get("version_id"),
        "thread_id": state.get("thread_id", ""),
        "recall_tags": state.get("recall_tags", {}),
        "extract_tags": {},
        "validated_tags": {},
        "current_tag_code": "",
    }
    subgraph = build_extract_subgraph()
    compiled = subgraph.compile()
    result = await compiled.ainvoke(sub_state)
    extract_tags = result.get("extract_tags", {})
    if not extract_tags:
        extract_tags = result.get("valid_tags", result.get("validated_tags", {}))
    recall_tags = result.get("recall_tags", state.get("recall_tags", {}))
    logger.info(f"[extract_node] extract_tags={len(extract_tags)}")
    return {
        "recall_tags": recall_tags,
        "extract_tags": extract_tags,
        "validated_tags": extract_tags,
        "current_stage": "extract",
        "stage_status": {"extract": "done"},
    }
