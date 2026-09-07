"""LangGraph PostgreSQL checkpointer 的部署适配器。

审批暂停点属于工作流运行时状态，不应塞进 Redis；使用 PostgreSQL 可使 API、ARQ Worker
重启后仍能根据同一 thread_id 恢复执行。具体业务状态仍由各领域自己的表保存。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.core.config import Settings


def to_psycopg_url(database_url: str) -> str:
    """将 SQLAlchemy 异步 URL 转成 checkpointer 所需的 psycopg 连接 URL。"""
    return database_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgresql+psycopg://", "postgresql://"
    )


@asynccontextmanager
async def open_checkpoint_saver(settings: Settings) -> AsyncIterator[AsyncPostgresSaver]:
    """打开一次短生命周期 saver；部署初始化由独立 CLI 负责，运行时不执行 DDL。"""
    async with AsyncPostgresSaver.from_conn_string(to_psycopg_url(settings.database_url)) as saver:
        yield saver
