"""初始化 LangGraph PostgreSQL checkpointer 表。"""

import asyncio

from app.core.config import get_settings
from app.integrations.langgraph_checkpoint import open_checkpoint_saver


async def setup() -> None:
    """只创建 LangGraph 自己的运行时表；业务表仍完全由 Alembic 管理。"""
    async with open_checkpoint_saver(get_settings()) as saver:
        await saver.setup()


def main() -> None:
    asyncio.run(setup())
    print("LangGraph checkpointer 初始化完成")


if __name__ == "__main__":
    main()
