"""Bid-Wise 后端应用。"""

import asyncio
import sys

# psycopg 的异步实现不支持 Windows 默认 ProactorEventLoop。必须在创建 API/Worker
# 事件循环前设置策略；Linux 容器不受此分支影响。
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
