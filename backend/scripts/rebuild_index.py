"""重建向量索引：清空旧 chunk 后按新切块逻辑（整篇喂入）重新索引。

用法（在 backend 目录下）：
    uv run python scripts/rebuild_index.py                      # 重建所有已索引版本
    uv run python scripts/rebuild_index.py <version_id> [...]   # 只重建指定版本
    uv run python scripts/rebuild_index.py --dry-run            # 只统计，不执行
"""

from __future__ import annotations

import argparse
import logging
import sys
from uuid import UUID

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.integrations.ai.embedding import BgeM3Client
from app.integrations.ai.llm import DeepSeekV4FlashClient
from app.integrations.vector_store import PgVectorStore
from app.services.document_indexing_service import DocumentIndexingService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main() -> int:
    parser = argparse.ArgumentParser(description="重建向量索引（整篇喂入切块）")
    parser.add_argument("version_ids", nargs="*", help="指定 document_version_id，缺省为全部已索引版本")
    parser.add_argument("--dry-run", action="store_true", help="只统计待重建版本，不执行")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        logger.error("DATABASE_URL 未配置")
        return 1
    engine = create_engine(settings.database_url)

    with Session(engine) as session:
        if args.version_ids:
            version_ids = [UUID(v) for v in args.version_ids]
        else:
            # 默认重建所有已完成索引的版本（READY/SUCCEEDED）
            version_ids = list(
                session.scalars(
                    text(
                        "SELECT id FROM app.document_versions "
                        "WHERE parse_status IN ('READY', 'SUCCEEDED') ORDER BY created_at"
                    )
                )
            )
        logger.info("待重建版本数: %d", len(version_ids))
        if args.dry_run or not version_ids:
            return 0

        service = DocumentIndexingService(
            session,
            BgeM3Client(settings),
            PgVectorStore(settings),
            llm_client=DeepSeekV4FlashClient(settings),
        )
        failed = 0
        for i, version_id in enumerate(version_ids, 1):
            try:
                # 硬删旧 chunk（本地开发，无需保留历史），do_index 会重新生成
                deleted = session.execute(
                    text(
                        "DELETE FROM app.search_chunks "
                        "WHERE source_document_version_id = :vid"
                    ),
                    {"vid": version_id},
                ).rowcount
                session.commit()
                chunks = service.do_index(version_id)
                logger.info(
                    "[%d/%d] version=%s 旧chunk=%d 新chunk=%d",
                    i, len(version_ids), version_id, deleted, len(chunks),
                )
            except Exception:
                session.rollback()
                failed += 1
                logger.exception("[%d/%d] version=%s 重建失败", i, len(version_ids), version_id)
        logger.info("完成: 成功 %d / 失败 %d", len(version_ids) - failed, failed)
        return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
