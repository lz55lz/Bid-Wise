"""chunk_store — bid_pipeline 对 document_nodes 的统一读取层。

替代旧 bid_doc_chunk 直查。约定：
  - chunk_id 一律是 document_nodes.id（UUID 字符串）
  - chunk_index 对应 document_nodes.order_no
  - category_codes / candidate_tags 存于 metadata_ JSONB
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

_CHUNK_COLUMNS = """
    id::text AS chunk_id,
    order_no AS chunk_index,
    content AS chunk_text,
    COALESCE(section_path, '') AS section_path,
    LOWER(node_type::text) AS chunk_type,
    page_number AS page_no,
    tender_req_candidate,
    COALESCE(metadata -> 'category_codes', '[]'::jsonb) AS category_codes,
    COALESCE(metadata -> 'candidate_tags', '[]'::jsonb) AS candidate_tags
"""


def _normalize(rows: list[tuple]) -> list[dict[str, Any]]:
    return [
        {
            "chunk_id": r[0],
            "chunk_index": r[1],
            "chunk_text": r[2] or "",
            "section_path": r[3] or "",
            "chunk_type": r[4] or "paragraph",
            "page_no": r[5],
            "tender_req_candidate": bool(r[6]),
            "category_codes": list(r[7] or []),
            "candidate_tags": list(r[8] or []),
        }
        for r in rows
    ]


def fetch_chunks(
    session: Session,
    version_id: UUID | str,
    only_candidate: bool = False,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """按 version 读取全部（或仅候选）节点，按 order_no 排序。"""
    stmt = f"""
        SELECT {_CHUNK_COLUMNS}
        FROM app.document_nodes
        WHERE document_version_id = :version_id
          AND COALESCE(metadata ->> 'rechunk_superseded', 'false') <> 'true'
          {"AND tender_req_candidate = true" if only_candidate else ""}
        ORDER BY order_no
        {"LIMIT :lim" if limit is not None else ""}
    """
    params: dict[str, Any] = {"version_id": str(version_id)}
    if limit is not None:
        params["lim"] = limit
    return _normalize(session.execute(text(stmt), params).fetchall())


def fetch_chunks_by_ids(
    session: Session, chunk_ids: list[str]
) -> list[dict[str, Any]]:
    """按节点 UUID 列表读取，保持文档顺序。"""
    if not chunk_ids:
        return []
    stmt = text(f"""
        SELECT {_CHUNK_COLUMNS}
        FROM app.document_nodes
        WHERE id = ANY(:ids)
        ORDER BY order_no
    """).bindparams()
    rows = session.execute(stmt, {"ids": [str(c) for c in chunk_ids]}).fetchall()
    return _normalize(rows)


def fetch_chunk_texts(session: Session, chunk_ids: list[str]) -> dict[str, str]:
    """chunk_id -> content 映射。"""
    chunks = fetch_chunks_by_ids(session, chunk_ids)
    return {c["chunk_id"]: c["chunk_text"] for c in chunks}


def set_candidate_tags(
    session: Session, version_id: UUID | str, candidate_tags: dict[str, list[str]]
) -> int:
    """把 tagging 结果写入 metadata_.candidate_tags，返回更新行数。"""
    total = 0
    stmt = text("""
        UPDATE app.document_nodes
        SET metadata = jsonb_set(
                COALESCE(metadata, '{}'), '{candidate_tags}', to_jsonb(CAST(:tags AS text[]))
            )
        WHERE document_version_id = :version_id AND id = :chunk_id
    """)
    for chunk_id, tags in candidate_tags.items():
        result = session.execute(
            stmt, {"version_id": str(version_id), "chunk_id": chunk_id, "tags": tags}
        )
        total += result.rowcount
    return total


def set_tender_candidates(
    session: Session, version_id: UUID | str, chunk_indexes: list[int]
) -> int:
    """Replace the clean-stage candidate set, including stale candidates from reruns."""
    # Re-running clean after a parser or policy update must be able to shrink
    # the queue. The previous write-only-true implementation left candidates
    # set by the broad annotation stage permanently selected.
    session.execute(
        text("""
            UPDATE app.document_nodes
            SET tender_req_candidate = false
            WHERE document_version_id = :version_id
              AND tender_req_candidate = true
        """),
        {"version_id": str(version_id)},
    )
    if not chunk_indexes:
        return 0
    result = session.execute(
        text("""
            UPDATE app.document_nodes
            SET tender_req_candidate = true
            WHERE document_version_id = :version_id AND order_no = ANY(:indexes)
        """),
        {"version_id": str(version_id), "indexes": [int(i) for i in chunk_indexes]},
    )
    return result.rowcount
