"""应用进程级数据库连接池与请求级 AsyncSession。"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """按进程惰性创建连接池，禁止在路由、服务或仓储里自行建池。"""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """返回可复用工厂；每个请求从中创建自己的短生命周期 Session。"""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖项：正常请求提交，异常请求回滚并始终关闭 Session。"""
    # ``get_session_factory`` 返回的是工厂本身；必须再调用一次才得到单请求 Session。
    # Worker 已使用 ``get_session_factory()()``，HTTP 依赖也必须保持同一生命周期语义。
    async with get_session_factory()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_database_engine() -> None:
    """应用关闭时释放连接池，避免开发热重载或 Worker 停止时遗留连接。"""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
