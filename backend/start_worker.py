# -*- coding: utf-8 -*-
"""
ARQ Worker 启动脚本

Windows + uv 环境启动方式：
    cd backend
    python start_worker.py

或者一行命令：
    python -c "
import sys, os
os.chdir(r'D:\\workspace\\python\\lei\\backend')
sys.path.insert(0, '.')
if sys.platform == 'win32':
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from app.worker import WorkerSettings, run_bid_pipeline
from arq.worker import Worker
w = Worker(functions=[run_bid_pipeline], redis_settings=WorkerSettings.get_redis_settings(), max_jobs=3)
w.run()
"

注意：不要用 asyncio.run() 包装 Worker.run()，Windows 上会冲突。
"""
import sys
import os

# Windows asyncio 兼容（必须在最开头设置）
if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
)

if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, ".")

    from app.worker import WorkerSettings
    from arq.worker import Worker

    w = Worker(
        functions=WorkerSettings.functions,
        redis_settings=WorkerSettings.get_redis_settings(),
        max_jobs=WorkerSettings.max_jobs,
        keep_result=WorkerSettings.keep_result,
    )
    logging.info("ARQ Worker starting, redis=%s", WorkerSettings.get_redis_settings())
    w.run()
