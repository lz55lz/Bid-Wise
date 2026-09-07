"""FastAPI 应用入口；业务接口按领域从 api/v1/router.py 注册。"""

from contextlib import asynccontextmanager
import logging
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request

from app.api.v1.router import router as v1_router
from app.core.config import get_settings
from app.core.errors import DomainError, domain_error_handler, unexpected_error_handler
from app.db.session import dispose_database_engine
from app.integrations.http_client import close_http_clients
from app.integrations.redis_pool import close_redis_pool
from app.modules.system.service import DependencyUnavailable, ReadinessService


logger = logging.getLogger("bidwise.request")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """进程级资源只在此初始化与关闭，禁止在每个请求内创建连接池。"""
    yield
    await close_redis_pool()
    await close_http_clients()
    await dispose_database_engine()


app = FastAPI(title="Bid-Wise API", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(DomainError, domain_error_handler)
app.add_exception_handler(Exception, unexpected_error_handler)
app.include_router(v1_router)


@app.middleware("http")
async def request_context(request: Request, call_next):
    """为每个请求生成不可伪造的关联 ID，并记录稳定的结构化访问摘要。"""
    request_id = uuid4().hex
    request.state.request_id = request_id
    started = perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        raise
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "elapsed_ms": round((perf_counter() - started) * 1000, 2),
        },
    )
    return response


@app.get("/healthz", tags=["system"])
async def healthz() -> dict[str, str]:
    """无鉴权进程存活探针；不检查外部依赖。"""
    return {"status": "ok"}


@app.get("/readyz", tags=["system"])
async def readyz() -> dict[str, str]:
    """无鉴权就绪探针；只确认核心依赖连接，不泄漏连接地址或故障细节。"""
    try:
        await ReadinessService(get_settings()).check()
    except DependencyUnavailable as exc:
        raise DomainError("SERVICE_NOT_READY", "服务依赖尚未就绪", 503) from exc
    return {"status": "ready"}
