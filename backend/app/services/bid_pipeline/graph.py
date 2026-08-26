"""Bid Pipeline Graph — LangGraph StateGraph 组装

拓扑：START → parse → chunk → clean → annotate → index → tagging → extract → validate → END。

文档入库只负责解析、清洗、索引和候选抽取；风险、材料匹配、决策和报告统一由
`AnalysisService` 的项目分析任务执行，避免同一份文档出现两套事实输出。
"""

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from langgraph.graph import END, StateGraph
from sqlalchemy import text

from app.core.config import get_settings
from app.services.bid_pipeline.clean_service import clean_node
from app.services.bid_pipeline.extract_subgraph import extract_node
from app.services.bid_pipeline.index_node import index_node
from app.services.bid_pipeline.preprocess import annotate_node
from app.services.bid_pipeline.state import BidState
from app.services.bid_pipeline.tagging_node import tagging_node
from app.services.bid_pipeline.validate_node import validate_node

logger = logging.getLogger(__name__)


def _parse_node(state: BidState) -> dict[str, Any]:
    """解析文档：MinIO 下载 → 三级 fallback 解析，写入 document_nodes。

    读取 document_versions.object_key 下载 PDF，解析后写入 document_nodes。
    解析+下载复用 document_ingest 包，节点本身只负责"查元数据 + 写业务表"。
    """
    from pathlib import Path
    from uuid import UUID as PyUUID

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.services.document_ingest import (
        cleanup_temp_file,
        download_document,
        parse_document,
    )

    version_id: PyUUID | None = state.get("version_id")
    doc_id = state["doc_id"]
    session = get_session_factory()()
    settings = get_settings()
    file_path: str | None = None
    mime_type: str | None = None

    try:
        if version_id is None:
            # fallback: 从 bid_document 查（兼容旧数据）
            row = session.execute(
                text(
                    "SELECT doc_url, raw_text_path, doc_type "
                    "FROM bid_document WHERE doc_id = :doc_id"
                ),
                {"doc_id": doc_id},
            ).fetchone()
            if not row:
                return {
                    "chunks": [],
                    "raw_text": "",
                    "parse_status": "error",
                    "parse_error": "文档记录不存在",
                }
            doc_url, raw_text_path, mime_type = row[0], row[1], row[2]
        else:
            # 新链路：从 document_versions 读 object_key
            row = session.execute(
                text(
                    "SELECT dv.object_key, dv.file_name, dv.mime_type, d.id "
                    "FROM app.document_versions dv "
                    "JOIN app.documents d ON d.id = dv.document_id "
                    "WHERE dv.id = :version_id"
                ),
                {"version_id": str(version_id)},
            ).fetchone()
            if not row:
                return {
                    "chunks": [],
                    "raw_text": "",
                    "parse_status": "error",
                    "parse_error": "文档版本记录不存在",
                }
            object_key, file_name, mime_type = row[0], row[1], row[2]
            doc_url = f"minio://{settings.minio_bucket}/{object_key}" if object_key else None
            raw_text_path = None

        # 下载（document_ingest 统一入口）
        file_path = download_document(
            raw_text_path=raw_text_path,
            doc_url=doc_url,
            file_name=file_name if version_id is not None else None,
            settings=settings,
        )
        if not file_path or not Path(file_path).exists():
            return {
                "chunks": [],
                "raw_text": "",
                "parse_status": "error",
                "parse_error": "无法从对象存储下载文件",
            }

        mime_type = mime_type or "application/pdf"
        chunks, raw_text = parse_document(file_path, mime_type, doc_id)

        if not chunks:
            logger.error(f"[_parse_node] doc_id={doc_id}: all parsers failed, chunks=0")
            return {
                "chunks": [],
                "raw_text": "",
                "parse_status": "error",
                "parse_error": "解析器未产出文本",
            }

        _prepare_version_for_rechunk(session, version_id)
        # 写入 document_nodes，并把内存 chunks 的 chunk_id 重写为节点 UUID，
        # 使下游阶段（clean/tagging/extract/report）的 chunk_id 与 DB 行一一对应
        node_id_map = _write_nodes_to_document(session, version_id, chunks)
        # 每个节点建一条 Evidence：问答索引（DocumentIndexingService）的子块
        # 锚定和引用回查都依赖它，缺失会导致索引只建父块不建子块
        _write_evidences_for_nodes(session, version_id, chunks, node_id_map)
        for ch in chunks:
            node_id = node_id_map.get(ch["chunk_index"])
            if node_id is not None:
                ch["chunk_id"] = node_id
        session.commit()
        logger.info(f"[_parse_node] doc_id={doc_id}: parsed {len(chunks)} nodes, status=done")
        return {"chunks": chunks, "raw_text": raw_text, "parse_status": "done"}
    except Exception as e:
        logger.exception(f"[_parse_node] doc_id={doc_id}: {e}")
        return {"chunks": [], "raw_text": "", "parse_status": "error", "parse_error": str(e)[:500]}
    finally:
        session.close()
        cleanup_temp_file(file_path)


def _write_evidences_for_nodes(
    session, version_id, chunks: list[dict], node_id_map: dict[int, str]
) -> None:
    """为每个 document_node 生成一条 DOCUMENT_TEXT Evidence（幂等：按节点已存在则跳过）。"""
    if version_id is None:
        return
    from uuid import uuid4 as uuid_fn

    existing = {
        row[0]
        for row in session.execute(
            text(
                "SELECT document_node_id::text FROM app.evidences "
                "WHERE document_version_id = :v AND document_node_id IS NOT NULL"
            ),
            {"v": str(version_id)},
        ).fetchall()
    }
    for ch in chunks:
        node_id = node_id_map.get(ch["chunk_index"])
        if node_id is None or node_id in existing:
            continue
        quoted = (ch.get("chunk_text") or "")[:200]
        session.execute(
            text("""
                INSERT INTO app.evidences
                (id, source_type, document_version_id, document_node_id,
                 page_number, quoted_text, content_hash, source_reference, created_at)
                VALUES
                (:id, 'DOCUMENT_TEXT', :version_id, :node_id,
                 :page_no, :quoted, :content_hash, '{}'::jsonb, :created_at)
            """),
            {
                "id": str(uuid_fn()),
                "version_id": str(version_id),
                "node_id": node_id,
                "page_no": ch.get("page_no"),
                "quoted": quoted,
                "content_hash": hashlib.sha256(quoted.encode()).hexdigest(),
                "created_at": datetime.now(UTC),
            },
        )
    # Reparse can reuse node IDs. Keep their Evidence citation metadata in
    # sync rather than preserving a stale NULL page number from an older run.
    session.execute(
        text(
            """
            UPDATE app.evidences e
            SET page_number = n.page_number,
                quoted_text = left(n.content, 200),
                content_hash = n.content_hash
            FROM app.document_nodes n
            WHERE e.document_version_id = :version_id
              AND e.document_node_id = n.id
              AND n.document_version_id = :version_id
            """
        ),
        {"version_id": str(version_id)},
    )


def _prepare_version_for_rechunk(session, version_id: UUID | None) -> None:
    """Invalidate derived retrieval state before a same-version parser rerun.

    A successful document may be rebuilt after chunking rules improve.  We
    retain source rows for audit but hide leftover layout fragments from the
    reader and all downstream stages; newly written order numbers overwrite
    their rows and clear this marker.  Search chunks are derived data and must
    be recreated from the newly cleaned clause units.
    """
    if version_id is None:
        return
    session.execute(
        text("""
            UPDATE app.document_nodes
            SET tender_req_candidate = false,
                cleaning_metadata = COALESCE(cleaning_metadata, '{}'::jsonb)
                    || '{"indexable": false, "rechunk_superseded": true}'::jsonb,
                metadata = COALESCE(metadata, '{}'::jsonb)
                    || '{"rechunk_superseded": true}'::jsonb
            WHERE document_version_id = :version_id
        """),
        {"version_id": str(version_id)},
    )
    session.execute(
        text("DELETE FROM app.search_chunks WHERE source_document_version_id = :version_id"),
        {"version_id": str(version_id)},
    )


def _parse_document(file_path: str, mime_type: str, doc_id: int) -> tuple[list[dict], str]:
    """三级 fallback 解析（已迁移到 app.services.document_ingest.parse_document）。

    保留此 shim 仅供历史测试代码导入兼容；新代码请直接用 document_ingest。
    """
    from app.services.document_ingest import parse_document as _parse

    return _parse(file_path, mime_type, doc_id)


def _write_nodes_to_document(
    session, version_id: UUID | None, chunks: list[dict]
) -> dict[int, str]:
    """批量写入 document_nodes，返回 order_no → 节点 UUID 映射。

    version_id 为 None 时跳过写入（兼容旧流程），返回空映射。
    """
    if version_id is None:
        return {}
    from uuid import uuid4 as uuid_fn

    node_id_map: dict[int, str] = {}
    for ch in chunks:
        # ``content`` is a source fact and the column is TEXT.  Never silently
        # truncate it here: unparseable large nodes are rejected by clean, not
        # mutilated before their Evidence can be audited.
        content = ch.get("chunk_text", "")
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        node_type = ch.get("chunk_type", "paragraph").upper()
        if node_type not in ("SECTION", "PARAGRAPH", "LIST", "TABLE", "CELL", "IMAGE"):
            node_type = "PARAGRAPH"
        # RETURNING 同时覆盖插入与冲突更新两种情况，重跑时仍拿到既有行的真实 id
        row = session.execute(
            text("""
          INSERT INTO app.document_nodes
          (id, document_version_id, node_type, page_number, section_path,
           order_no, content, content_hash, tender_req_candidate, bbox, metadata, created_at)
          VALUES
          (:id, :version_id, CAST(:node_type AS DOCUMENT_NODE_TYPE), :page_no, :section_path,
           :order_no, :content, :content_hash, false, CAST(:bbox AS jsonb),
           CAST(:metadata AS jsonb), :created_at)
          ON CONFLICT (document_version_id, order_no) DO UPDATE
          SET node_type = EXCLUDED.node_type,
              content = EXCLUDED.content,
              content_hash = EXCLUDED.content_hash,
              page_number = EXCLUDED.page_number,
              section_path = EXCLUDED.section_path,
              bbox = EXCLUDED.bbox,
              metadata = EXCLUDED.metadata
                RETURNING id::text, order_no
            """),
            {
                "id": str(uuid_fn()),
                "version_id": str(version_id),
                "node_type": node_type,
                "page_no": ch.get("page_no"),
                # Schema keeps a bounded display path, while JSON metadata
                # retains the full source hierarchy for clause context.
                "section_path": (ch.get("section_path") or "")[:1024],
                "order_no": ch["chunk_index"],
                "content": content,
                  "content_hash": content_hash,
                  "bbox": json.dumps(ch.get("bbox")) if ch.get("bbox") is not None else None,
                  "metadata": json.dumps({
                      "source_chunk_type": ch.get("chunk_type", "paragraph"),
                      "source_section_path": ch.get("section_path") or "",
                      **(ch.get("parser_metadata") or {}),
                  }),
                  "created_at": datetime.now(UTC),
            },
        ).fetchone()
        if row is not None:
            node_id_map[row[1]] = row[0]
    return node_id_map


def _chunk_node(state: BidState) -> dict[str, Any]:
    """Pass-through: parse 阶段已写入 document_nodes，这里只推进 stage"""
    return {
        "chunks": state.get("chunks", []),
        "raw_text": state.get("raw_text", ""),
        "current_stage": "chunk",
        "stage_status": {"chunk": "done"},
    }


def build_bid_analysis_graph() -> StateGraph:
    builder = StateGraph(BidState)

    # === 节点 ===
    builder.add_node("parse", _parse_node)
    builder.add_node("chunk", _chunk_node)
    builder.add_node("clean", clean_node)
    builder.add_node("annotate", annotate_node)
    builder.add_node("index", index_node)
    builder.add_node("tagging", tagging_node)
    builder.add_node("extract", extract_node)
    builder.add_node("validate", validate_node)

    # === 边 ===
    from langgraph.graph import START

    builder.add_edge(START, "parse")

    # parse 失败时中断，不继续后续节点
    def route_after_parse(state: BidState) -> str:
        if state.get("parse_status") == "error":
            return "parse_failed"
        return "chunk_ok"

    builder.add_conditional_edges(
        "parse",
        route_after_parse,
        {"parse_failed": END, "chunk_ok": "chunk"},
    )
    builder.add_edge("chunk", "clean")
    builder.add_edge("clean", "annotate")
    builder.add_edge("annotate", "index")
    builder.add_edge("index", "tagging")
    builder.add_edge("tagging", "extract")
    builder.add_edge("extract", "validate")

    builder.add_edge("validate", END)

    return builder


_checkpointer: Any = None
_compiled_graph: Any = None


def _init_checkpointer() -> Any:
    """初始化 sync PostgresSaver checkpointer（仅调用一次）"""
    global _checkpointer
    if _checkpointer is None:
        from langgraph.checkpoint.postgres import PostgresSaver

        settings = get_settings()
        url = settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)
        _checkpointer = PostgresSaver.from_conn_string(url)
        _checkpointer.setup()
    return _checkpointer


def get_compiled_graph(async_checkpoint: bool = False):
    """返回编译好的 graph（首次编译后缓存）"""
    global _compiled_graph
    if _compiled_graph is None:
        builder = build_bid_analysis_graph()
        if async_checkpoint:
            checkpointer = _init_checkpointer()
            _compiled_graph = builder.compile(
                checkpointer=checkpointer,
                interrupt_before=["human_review"],
            )
        else:
            _compiled_graph = builder.compile()
    return _compiled_graph
