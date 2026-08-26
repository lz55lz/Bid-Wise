"""RAG 流式回答统一生成器。

基于 LangChain 原生 astream 的全异步单次 LLM 调用：
- 检索由调用方完成（KnowledgeRagService / RagService），传入 contexts
- 本模块负责流式输出 delta 事件和最终 done 事件（SSE 格式）
- citations 直接取检索到的证据（与 WeKnora 一致），不再做第二次结构化 LLM 调用，
  避免双倍延迟以及 function_calling 失败导致整段无输出的问题
"""

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from app.integrations.ai.llm import RagLlm

logger = logging.getLogger(__name__)

NO_EVIDENCE_ANSWER = "未找到证据"


def sse_event(data: dict[str, Any]) -> bytes:
    """编码一条 SSE data 事件。"""
    return ("data: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8")


async def stream_rag_answer(
    llm: RagLlm,
    question: str,
    contexts: list[dict[str, Any]],
) -> AsyncIterator[bytes]:
    """流式输出 RAG 回答（SSE 事件字节流）。

    事件序列：
    - contexts 为空 → 单个 done(no_evidence=true)
    - 正常 → 若干 delta + 单个 done(answer=完整回答, citations=检索证据)
    - LLM 流失败且无任何输出 → error 事件
    - LLM 流中途失败但已有输出 → 用已收到的内容收尾（降级，不报错）
    """
    logger.info("[RAG stream] start: question=%r contexts=%d", question[:50], len(contexts))
    if not contexts:
        yield sse_event({
            "type": "done",
            "answer": NO_EVIDENCE_ANSWER,
            "no_evidence": True,
            "citations": [],
        })
        return

    full_answer = ""
    chunk_count = 0
    try:
        logger.info("[RAG stream] LLM streaming start")
        async for chunk in llm.astream_answer(question, contexts):
            if not chunk:
                continue
            chunk_count += 1
            full_answer += chunk
            yield sse_event({"type": "delta", "content": chunk})
        logger.info("[RAG stream] LLM streaming done: chunks=%d answer_len=%d", chunk_count, len(full_answer))
    except Exception as exc:
        logger.error("[RAG stream] LLM stream error: %s: %s", type(exc).__name__, exc)
        if not full_answer:
            yield sse_event({"type": "error", "message": f"LLM 流式输出失败: {exc}"})
            return
        logger.warning("[RAG stream] partial answer kept: chunks=%d len=%d", chunk_count, len(full_answer))

    answer = full_answer.strip()
    no_evidence = not answer or answer == NO_EVIDENCE_ANSWER
    citations = [] if no_evidence else [
        {"evidence_id": str(c["evidence_id"]), "content": str(c["content"])[:200]}
        for c in contexts
    ]
    logger.info("[RAG stream] done: answer=%r no_evidence=%s citations=%d", answer[:100], no_evidence, len(citations))
    yield sse_event({
        "type": "done",
        "answer": full_answer,
        "no_evidence": no_evidence,
        "citations": citations,
    })