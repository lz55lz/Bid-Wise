"""本地 API 启动入口。

Windows 下必须在导入 Uvicorn 前切换到 SelectorEventLoop；仅在 ``app.__init__``
中设置已太晚，因为 Uvicorn CLI 可能先创建默认 Proactor 事件循环。
"""

import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn


def main() -> None:
    # ``uvicorn.run`` 会按平台再次选择事件循环；这里交由已经设置 Selector 策略的
    # asyncio.run 创建循环，避免 Windows 上回退为 ProactorEventLoop。
    config = uvicorn.Config("app.main:app", host="127.0.0.1", port=8000, loop="none")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    main()
