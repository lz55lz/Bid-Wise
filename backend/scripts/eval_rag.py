# -*- coding: utf-8 -*-
"""RAG 召回质量评测：recall@5 / MRR。

用法：
    uv run python scripts/eval_rag.py [--set scripts/rag_eval_set.json] [--topk 5]

评测集格式（JSON 数组）：
    [
      {"scope": "knowledge", "question": "...", "expect": ["命中子串", ...]},
      {"scope": "project", "question": "...", "expect": ["..."]}
    ]
    - expect 任一子串出现在某条召回内容中即记命中（该条 rank 计 MRR）
    - project 条目默认用库里最新的 TENDER 项目，可用 --project <uuid> 指定

只跑检索+重排（无 LLM 生成），不消耗 LLM 配额。
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import create_engine, text

from app.core.config import get_settings
from app.db.session import get_session_factory


def _norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\u3000", "")


def _hit_at(contexts: list, expects: list[str]) -> int | None:
    """返回首个命中的 1-based rank；未命中返回 None。"""
    for rank, ctx in enumerate(contexts, start=1):
        content = _norm(_ctx_text(ctx))
        if any(_norm(e) in content for e in expects):
            return rank
    return None


def _ctx_text(ctx) -> str:
    # KnowledgeRagService._Context: .content；RagService._Context: .chunk.content
    return getattr(ctx, "content", None) or getattr(getattr(ctx, "chunk", None), "content", "")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", default="scripts/rag_eval_set.json")
    parser.add_argument("--topk", type=int, default=5)
    parser.add_argument("--project", default=None, help="项目 UUID（project 条目用）")
    args = parser.parse_args()

    entries = json.loads(Path(args.set).read_text(encoding="utf-8"))
    settings = get_settings()

    from app.db.repositories.search_repository import SearchRepository
    from app.integrations.ai.embedding import BgeM3Client
    from app.integrations.ai.llm import DeepSeekV4FlashClient
    from app.integrations.ai.reranker import BgeRerankerV2M3Client
    from app.integrations.vector_store import PgVectorStore
    from app.services.knowledge_rag_service import KnowledgeRagService
    from app.services.rag_service import RagService

    session = get_session_factory()()
    try:
        with create_engine(settings.database_url).connect() as c:
            admin = c.execute(
                text("SELECT id FROM app.users ORDER BY created_at LIMIT 1")
            ).scalar()
            project_id = args.project or c.execute(
                text("""
                    SELECT d.project_id FROM app.documents d
                    JOIN app.document_versions v ON v.document_id = d.id
                    JOIN app.search_chunks sc ON sc.source_document_version_id = v.id
                    WHERE d.project_id IS NOT NULL AND sc.deleted_at IS NULL
                    GROUP BY d.project_id ORDER BY max(v.created_at) DESC LIMIT 1
                """)
            ).scalar()
        print(f"[eval] project={project_id} entries={len(entries)} topk={args.topk}")

        k_service = KnowledgeRagService(
            session, settings, BgeM3Client(settings), PgVectorStore(settings),
            BgeRerankerV2M3Client(settings), llm=None,
        )
        p_service = RagService(
            session, settings, BgeM3Client(settings), PgVectorStore(settings),
            BgeRerankerV2M3Client(settings), DeepSeekV4FlashClient(settings),
        )

        results = []
        for i, entry in enumerate(entries, start=1):
            q = entry["question"]
            expects = entry["expect"]
            scope = entry["scope"]
            try:
                if scope == "knowledge":
                    vec = k_service._embed_question(q)
                    ctxs = k_service._retrieve(q, vec, None, admin, {"SYSTEM_ADMIN"})
                    ranked = k_service._rank(q, ctxs) if ctxs else []
                else:
                    from app.services.query_rewrite_service import rewrite_query

                    rr = rewrite_query(q)
                    vec = p_service._embed_question(q, rr)
                    ctxs = p_service._retrieve(q, vec, project_id, admin, {"SYSTEM_ADMIN"}, rr)
                    ranked = ctxs  # 项目侧 _retrieve 已按融合序返回
                rank = _hit_at(ranked[: max(args.topk, 20)], expects)
                top_rank = _hit_at(ranked[: args.topk], expects) if ranked else None
                ok = top_rank is not None
                mrr = 1.0 / top_rank if top_rank else 0.0
                results.append((scope, q, ok, top_rank, mrr))
                mark = "PASS" if ok else "FAIL"
                pos = f"@{top_rank}" if top_rank else "(>topk)"
                print(f"  [{i:>2}] {mark} {pos:<6} {scope:<8} {q}")
            except Exception as exc:
                results.append((scope, q, False, None, 0.0))
                print(f"  [{i:>2}] ERR   {scope:<8} {q} -> {exc}")

        for scope_name in ("knowledge", "project"):
            scoped = [r for r in results if r[0] == scope_name]
            if not scoped:
                continue
            hits = [r for r in scoped if r[2]]
            recall = len(hits) / len(scoped)
            mrr = sum(r[4] for r in scoped) / len(scoped)
            print(f"\n[{scope_name}] n={len(scoped)} recall@{args.topk}={recall:.2f} MRR={mrr:.3f}")
        total_mrr = sum(r[4] for r in results) / len(results)
        total_recall = sum(1 for r in results if r[2]) / len(results)
        print(f"[TOTAL] n={len(results)} recall@{args.topk}={total_recall:.2f} MRR={total_mrr:.3f}")
    finally:
        session.close()


asyncio.run(main())
