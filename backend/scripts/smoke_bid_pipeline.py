# -*- coding: utf-8 -*-
"""端到端冒烟：以 worker 相同方式直接跑 bid_pipeline 全图。

用法：uv run python scripts/smoke_bid_pipeline.py <pdf路径>
前提：PG/MinIO/(可选 MinerU+LLM) 已启动。
"""
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_session_factory
from app.integrations.object_storage import MinioObjectStorage
from app.services.bid_pipeline.graph import get_compiled_graph
from app.services.bid_pipeline.state import BidState

PDF_PATH = sys.argv[1] if len(sys.argv) > 1 else r"D:\Desktop\test\zb10.pdf"


async def main() -> None:
    settings = get_settings()
    storage = MinioObjectStorage(settings)
    session = get_session_factory()()

    with session.execute(text("SELECT id FROM app.users ORDER BY created_at LIMIT 1")) as r:
        actor_id = r.scalar()
    assert actor_id, "no user in db"
    project_id = uuid4()
    session.execute(
        text("""
            INSERT INTO app.tender_projects
            (id, name, code, purchaser, project_type, region, owner_id, created_at, updated_at)
            VALUES (:id, :name, :code, :purchaser, '工程', '测试', :actor, :now, :now)
        """),
        {"id": str(project_id), "name": "冒烟测试项目", "code": f"SMOKE-{project_id.hex[:8]}",
         "purchaser": "冒烟采购人", "actor": str(actor_id), "now": datetime.now(UTC)},
    )
    document_id, version_id = uuid4(), uuid4()
    object_key = f"documents/{document_id}/{version_id}/source"
    storage.put_file(object_key, Path(PDF_PATH), "application/pdf")

    session.execute(
        text("""
            INSERT INTO app.documents (id, project_id, document_type, logical_name, created_at, created_by)
            VALUES (:id, :pid, 'TENDER', :name, :now, :actor)
        """),
        {"id": str(document_id), "pid": str(project_id), "name": Path(PDF_PATH).name,
         "now": datetime.now(UTC), "actor": str(actor_id)},
    )
    session.execute(
        text("""
            INSERT INTO app.document_versions
            (id, document_id, version_no, file_name, file_size, mime_type, object_key,
             sha256, parse_status, pipeline_thread_id, created_at, created_by)
            VALUES (:id, :did, 1, :fname, :size, 'application/pdf', :key, :sha,
                    'QUEUED', :thread, :now, :actor)
        """),
        {
            "id": str(version_id), "did": str(document_id), "fname": Path(PDF_PATH).name,
            "size": Path(PDF_PATH).stat().st_size,
            "sha": __import__("hashlib").sha256(open(PDF_PATH, "rb").read()).hexdigest(),
            "key": object_key, "thread": f"bid-{version_id}", "now": datetime.now(UTC),
            "actor": str(actor_id),
        },
    )
    session.commit()
    session.close()
    print(f"[smoke] document={document_id} version={version_id}")

    state = BidState(
        doc_id=0,
        version_id=version_id,
        project_id=project_id,
        doc_name=Path(PDF_PATH).name,
        parse_status="pending",
        raw_text="",
        enterprise_name="测试建筑有限公司",
        enterprise_id=7,
        thread_id=f"bid-{version_id}",
        current_stage="parse",
    )
    compiled = get_compiled_graph(async_checkpoint=False)
    config = {"configurable": {"thread_id": f"bid-{version_id}"}}

    stage_count = 0
    async for event in compiled.astream(state, config=config, stream_mode="values"):
        stage = event.get("current_stage", "unknown")
        stage_count += 1
        print(f"[smoke] stage {stage_count}: {stage} | parse={event.get('parse_status')} "
              f"| chunks={len(event.get('chunks') or [])} "
              f"| extract_tags={len(event.get('extract_tags') or {})} "
              f"| risks={len(event.get('risk_results') or [])}")

    # 结果落位检查
    session = get_session_factory()()
    try:
        checks = {
            "document_nodes": "SELECT count(*) FROM app.document_nodes WHERE document_version_id = :v",
            "candidate_nodes": "SELECT count(*) FROM app.document_nodes WHERE document_version_id = :v AND tender_req_candidate",
            "tags_with_candidate_meta": "SELECT count(*) FROM app.document_nodes WHERE document_version_id = :v AND metadata ? 'candidate_tags'",
            "bid_document_tag": "SELECT count(*) FROM bid_document_tag WHERE version_id = :v",
            "tag_values": "SELECT count(*) FROM bid_document_tag WHERE version_id = :v AND tag_value IS NOT NULL",
            "bid_risk": "SELECT count(*) FROM bid_risk WHERE version_id = :v",
            "bid_report": "SELECT count(*) FROM bid_report WHERE version_id = :v",
            "bid_task_log": "SELECT count(*) FROM bid_task_log WHERE version_id = :v",
        }
        print("\n[smoke] ==== 落位检查 ====")
        for name, sql in checks.items():
            n = session.execute(text(sql), {"v": str(version_id)}).scalar()
            print(f"  {name}: {n}")
        row = session.execute(
            text("SELECT decision, overall_score, risk_score, trap_score, length(report_md) FROM bid_report WHERE version_id = :v"),
            {"v": str(version_id)},
        ).fetchone()
        if row:
            print(f"  report: decision={row[0]} overall={row[1]} risk={row[2]} trap={row[3]} md_chars={row[4]}")
    finally:
        session.close()


asyncio.run(main())
