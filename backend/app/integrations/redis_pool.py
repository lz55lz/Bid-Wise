"""进程级 Redis/ARQ 连接池；队列发布和短期认证状态共享连接。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class _PoolState:
    pool: Any
    dsn: str
    borrowers: int = 0
    retired: bool = False


_state: _PoolState | None = None
_pool_lock = asyncio.Lock()


async def _get_or_create_state(redis_url: str) -> _PoolState:
    """取得当前可借用的连接池状态；调用方必须仍在锁内增加 borrower。"""
    global _state
    if _state is not None and _state.dsn == redis_url and not _state.retired:
        return _state

    old_state = _state
    _state = None
    if old_state is not None:
        old_state.retired = True
    pool = await create_pool(RedisSettings.from_dsn(redis_url))
    _state = _PoolState(pool=pool, dsn=redis_url)
    return _state


@asynccontextmanager
async def borrow_redis_pool(redis_url: str):
    """借用 Redis pool，避免一个失败请求关闭另一个正在使用的连接。

    ARQ 的 pool 由 API 的队列发布、限流和就绪检查共享。过去任一调用失败都会
    立刻 ``close`` 全局 pool，恰好在使用它的并发请求也会被打断。借用计数保证
    失效池只在最后一个使用者释放后关闭。
    """
    state: _PoolState
    async with _pool_lock:
        state = await _get_or_create_state(redis_url)
        state.borrowers += 1
    try:
        yield state.pool
    finally:
        pool_to_close: Any | None = None
        async with _pool_lock:
            state.borrowers -= 1
            if state.retired and state.borrowers == 0:
                pool_to_close = state.pool
        if pool_to_close is not None:
            await _close_pool(pool_to_close)


async def invalidate_redis_pool() -> None:
    """让后续调用新建 pool，不中断已借用该 pool 的并发请求。"""
    global _state
    pool_to_close: Any | None = None
    async with _pool_lock:
        state, _state = _state, None
        if state is not None:
            state.retired = True
            if state.borrowers == 0:
                pool_to_close = state.pool
    if pool_to_close is not None:
        await _close_pool(pool_to_close)


async def close_redis_pool() -> None:
    """进程退出时关闭 Redis pool。"""
    await invalidate_redis_pool()


async def _close_pool(pool: Any) -> None:
    try:
        await pool.close()
    except Exception:
        logger.warning("关闭失效 Redis 连接池失败", exc_info=True)
