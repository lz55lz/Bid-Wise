"""observability — bid_task_log 可观测性装饰器

@stage_task(stage_name) 自动记录：
  - 开始/结束时间、执行时长
  - 输入/输出摘要、错误信息

断点续跑：同 (version_id, thread_id, stage) 已 success 的阶段直接回放缓存结果。
缓存以 thread_id 为界——重试/重跑会生成新 thread，不会回放陈旧结果。
"""
import asyncio
import functools
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text

from app.db.session import get_async_session_factory
from app.services.bid_pipeline.state import BidState

logger = logging.getLogger(__name__)


def stage_task(stage_name: str, retries: int = 2):
    """装饰器：为节点自动写入 bid_task_log。

    用法：
        @stage_task("annotate")
        async def annotate_node(state):
            ...
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        async def wrapper(state: BidState) -> dict[str, Any]:
            version_id = state.get("version_id")
            thread_id = state.get("thread_id", "")
            if version_id is None:
                # 旧链路（无 version_id）不记录，避免脏数据
                return await fn(state) if asyncio.iscoroutinefunction(fn) else fn(state)

            factory = get_async_session_factory()

            # 断点续跑：检查该 stage 在当前 thread 是否已完成
            async with factory() as s:
                existing = await s.execute(
                    text("""
                        SELECT payload FROM bid_task_log
                        WHERE version_id = :version_id AND stage = :stage
                          AND thread_id = :thread_id AND status = 'success'
                        ORDER BY created_at DESC LIMIT 1
                    """),
                    {
                        "version_id": str(version_id),
                        "stage": stage_name,
                        "thread_id": thread_id,
                    },
                )
                row = existing.fetchone()
                if row and row[0]:
                    cached = row[0]
                    if isinstance(cached, str):
                        cached = json.loads(cached)
                    logger.info(f"[{stage_name}] Skipping, already completed (cached)")
                    return cached

            # 写入 running 状态
            started_at = datetime.now(UTC)
            async with factory() as s:
                result = await s.execute(
                    text("""
                        INSERT INTO bid_task_log
                        (version_id, thread_id, stage, node_name, status,
                         started_at, max_attempts, created_at)
                        VALUES (:version_id, :thread_id, :stage, :node_name, 'running',
                                :started_at, :max_attempts, :started_at)
                        RETURNING task_id
                    """),
                    {
                        "version_id": str(version_id),
                        "thread_id": thread_id,
                        "stage": stage_name,
                        "node_name": stage_name,
                        "started_at": started_at,
                        "max_attempts": retries,
                    },
                )
                task_id = result.scalar()
                await s.commit()

            # 执行节点（兼容 sync / async）
            try:
                if asyncio.iscoroutinefunction(fn):
                    node_result = await fn(state)
                else:
                    node_result = fn(state)

                finished_at = datetime.now(UTC)
                duration_ms = int((finished_at - started_at).total_seconds() * 1000)

                # 写入 success（payload 序列化失败不影响节点结果本身）
                payload_json = None
                try:
                    payload_json = json.dumps(node_result, ensure_ascii=False, default=str)
                except (TypeError, ValueError) as exc:
                    logger.warning(f"[{stage_name}] payload not serializable: {exc}")

                async with factory() as s:
                    await s.execute(
                        text("""
                            UPDATE bid_task_log
                            SET status = 'success', finished_at = :finished_at,
                                duration_ms = :duration_ms,
                                output_summary = :summary,
                                payload = CAST(:payload AS jsonb)
                            WHERE task_id = :task_id
                        """),
                        {
                            "finished_at": finished_at,
                            "duration_ms": duration_ms,
                            "summary": str(node_result)[:500],
                            "payload": payload_json,
                            "task_id": task_id,
                        },
                    )
                    await s.commit()

                return node_result

            except Exception as e:
                finished_at = datetime.now(UTC)
                logger.warning(f"[{stage_name}] Failed: {e}")

                async with factory() as s:
                    await s.execute(
                        text("""
                            UPDATE bid_task_log
                            SET status = 'failed', finished_at = :finished_at,
                                error_msg = :error_msg
                            WHERE task_id = :task_id
                        """),
                        {
                            "finished_at": finished_at,
                            "error_msg": str(e)[:1000],
                            "task_id": task_id,
                        },
                    )
                    await s.commit()

                raise

        return wrapper

    return deco
