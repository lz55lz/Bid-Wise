"""清空测试环境业务数据：数据库业务表 + Celery 队列 + MinIO 对象。

保留内容（账号与基础配置）：
- users / roles / user_roles（登录账号）
- prompt_templates / rules / rule_versions（Prompt 与规则配置）
- integration_connectors（连接器配置）
- alembic_version（迁移版本）

用法（在 backend 目录下）：
    uv run python scripts/clear_test_data.py            # 交互确认后执行
    uv run python scripts/clear_test_data.py --yes      # 跳过确认
    uv run python scripts/clear_test_data.py --skip-minio --skip-redis
"""

from __future__ import annotations

import argparse
import sys

import psycopg

from app.core.config import get_settings

# 业务数据表（app schema），TRUNCATE ... CASCADE 无需关心外键顺序
BUSINESS_TABLES = [
    "ai_run_evidences",
    "ai_runs",
    "audit_logs",
    "checkpoint_blobs",
    "checkpoint_migrations",
    "checkpoint_writes",
    "checkpoints",
    "decision_evidences",
    "decisions",
    "document_nodes",
    "document_versions",
    "documents",
    "enterprise_materials",
    "evidences",
    "knowledge_entries",
    "knowledge_versions",
    "match_evidences",
    "match_overrides",
    "match_results",
    "material_documents",
    "project_fields",
    "project_members",
    "report_evidences",
    "report_sections",
    "reports",
    "requirement_evidences",
    "requirements",
    "risk_evidences",
    "risk_reviews",
    "risks",
    "search_chunks",
    "task_events",
    "tasks",
    "tender_projects",
]

# ARQ 队列（Redis key）
ARQ_QUEUE_KEYS = ["arq:queue"]


def _plain_database_url(database_url: str) -> str:
    """psycopg 直连不接受 SQLAlchemy 方言前缀。"""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def clear_database(database_url: str) -> None:
    with psycopg.connect(_plain_database_url(database_url), autocommit=False) as conn:
        with conn.cursor() as cur:
            # 清理前统计，便于确认影响范围
            print("[DB] 清理前行数：")
            total = 0
            for table in BUSINESS_TABLES:
                cur.execute(f"SELECT count(*) FROM app.{table}")
                count = cur.fetchone()[0]
                total += count
                if count:
                    print(f"  {table}: {count}")
            print(f"[DB] 合计 {total} 行")

            tables = ", ".join(f"app.{table}" for table in BUSINESS_TABLES)
            cur.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        conn.commit()
    print(f"[DB] 已清空 {len(BUSINESS_TABLES)} 张业务表")


def clear_redis_queues(redis_url: str) -> None:
    import redis

    client = redis.Redis.from_url(redis_url)
    deleted = client.delete(*ARQ_QUEUE_KEYS)
    # kombu 绑定元数据一并清掉，避免残留旧队列声明
    for key in client.scan_iter("_kombu.binding.*"):
        client.delete(key)
    client.close()
    print(f"[Redis] 已清空队列 {ARQ_QUEUE_KEYS}（删除 {deleted} 个 key）")


def clear_minio_objects(settings) -> None:
    from minio import Minio
    from minio.deleteobjects import DeleteObject

    endpoint = settings.minio_endpoint.replace("http://", "").replace("https://", "")
    client = Minio(
        endpoint,
        access_key=settings.minio_access_key.get_secret_value(),
        secret_key=settings.minio_secret_key.get_secret_value(),
        secure=settings.minio_endpoint.startswith("https"),
    )
    bucket = settings.minio_bucket
    if not client.bucket_exists(bucket):
        print(f"[MinIO] bucket {bucket} 不存在，跳过")
        return
    objects = [obj.object_name for obj in client.list_objects(bucket, recursive=True)]
    if not objects:
        print(f"[MinIO] bucket {bucket} 本来就是空的")
        return
    errors = list(
        client.remove_objects(bucket, [DeleteObject(name) for name in objects])
    )
    if errors:
        raise RuntimeError(f"MinIO 删除失败 {len(errors)} 个对象: {errors[0]}")
    print(f"[MinIO] 已删除 bucket {bucket} 中 {len(objects)} 个对象")


def main() -> int:
    parser = argparse.ArgumentParser(description="清空测试环境业务数据")
    parser.add_argument("--yes", action="store_true", help="跳过交互确认")
    parser.add_argument("--skip-redis", action="store_true", help="不清 Celery 队列")
    parser.add_argument("--skip-minio", action="store_true", help="不清 MinIO 对象")
    args = parser.parse_args()

    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL 未配置", file=sys.stderr)
        return 1

    db_name = settings.database_url.rsplit("/", 1)[-1]
    print(f"目标数据库: {db_name}")
    print(f"将清空 {len(BUSINESS_TABLES)} 张业务表")
    print("保留 users/roles/prompt_templates/rules 等配置表")
    if not args.skip_redis:
        print(f"将清空 Redis 队列: {ARQ_QUEUE_KEYS}")
    if not args.skip_minio:
        print(f"将清空 MinIO bucket: {settings.minio_bucket}")

    if not args.yes:
        confirm = input("确认执行？输入 yes 继续: ").strip().lower()
        if confirm != "yes":
            print("已取消")
            return 0

    clear_database(settings.database_url)
    if not args.skip_redis and settings.redis_url:
        clear_redis_queues(settings.redis_url)
    if not args.skip_minio and settings.minio_endpoint:
        clear_minio_objects(settings)

    print("完成。测试环境已清空，admin 账号与基础配置保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
