# Windows asyncio 兼容：必须在最开头设置
import sys

if sys.platform == "win32":
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from contextlib import asynccontextmanager
from logging import INFO, Formatter, getLogger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Lock
from time import monotonic
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.constants import MODEL_OVERRIDE_FIELDS
from app.core.errors import DomainError
from app.services.ai_health_service import AiHealthReport, AiHealthService

AI_HEALTH_CACHE_TTL_SECONDS = 10.0


def _configure_logging() -> None:
    """配置应用日志级别为 INFO，确保 RAG/LLM 关键节点日志可见。"""
    root = getLogger()
    root.setLevel(INFO)
    for name in ("app", "app.services", "app.api", "app.integrations"):
        getLogger(name).setLevel(INFO)
    # Third-party HTTP clients log complete request URLs at INFO.  Some provider
    # URLs contain short-lived credentials, so retain only warnings and errors.
    getLogger("httpx").setLevel("WARNING")
    log_path = Path(__file__).resolve().parents[2] / "logs" / "app.log"
    log_path.parent.mkdir(exist_ok=True)
    if not any(getattr(handler, "baseFilename", None) == str(log_path) for handler in root.handlers):
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)


_configure_logging()


def _check_ai_health() -> AiHealthReport:
    """Return a safe degraded status whenever deployment dependencies are unavailable."""
    from app.integrations.vector_store import PgVectorStore

    settings = get_settings()
    return AiHealthService(settings, PgVectorStore(settings)).check()


def _get_ai_health_report(app: FastAPI) -> AiHealthReport:
    """Refresh failed AI checks immediately and cache healthy checks briefly."""
    now = monotonic()
    report: AiHealthReport = app.state.ai_health
    checked_at: float | None = app.state.ai_health_checked_at
    if (
        report.available
        and checked_at is not None
        and now - checked_at < AI_HEALTH_CACHE_TTL_SECONDS
    ):
        return report

    with app.state.ai_health_lock:
        now = monotonic()
        report = app.state.ai_health
        checked_at = app.state.ai_health_checked_at
        if (
            report.available
            and checked_at is not None
            and now - checked_at < AI_HEALTH_CACHE_TTL_SECONDS
        ):
            return report
        report = _check_ai_health()
        app.state.ai_health = report
        app.state.ai_health_checked_at = now
        return report


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # AI failure must not prevent login, project management, or file reading.
        app.state.ai_health = _check_ai_health()
        app.state.ai_health_checked_at = monotonic()
        settings = get_settings()
        # ARQ Redis pool
        from arq import create_pool
        from arq.connections import RedisSettings

        if settings.redis_url:
            try:
                app.state.redis_pool = await create_pool(
                    RedisSettings.from_dsn(settings.redis_url)
                )
            except Exception:
                # Redis 仅承载队列和短期状态；不可因其暂时故障让登录、
                # 已有项目查看与健康诊断整个不可用。
                getLogger(__name__).exception("Redis unavailable; API starts in degraded mode")
                app.state.redis_pool = None
        else:
            app.state.redis_pool = None
        yield
        if app.state.redis_pool:
            await app.state.redis_pool.close()

    app = FastAPI(title="BidWise API", version="0.1.0", lifespan=lifespan)
    # Test clients and ASGI servers can serve a request before lifespan startup;
    # use a conservative default rather than treating configuration as healthy.
    app.state.ai_health = AiHealthReport(False, False, False, False)
    app.state.ai_health_checked_at = None
    app.state.ai_health_lock = Lock()

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID", str(uuid4()))
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                payload = await request.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and MODEL_OVERRIDE_FIELDS.intersection(payload):
                return JSONResponse(
                    status_code=400,
                    content={
                        "code": "MODEL_OVERRIDE_FORBIDDEN",
                        "message": "模型由服务端固定，不能通过请求覆盖",
                        "request_id": request.state.request_id,
                    },
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        invalid_fields = {str(error["loc"][-1]) for error in exc.errors()}
        if MODEL_OVERRIDE_FIELDS.intersection(invalid_fields):
            return JSONResponse(
                status_code=400,
                content={
                    "code": "MODEL_OVERRIDE_FORBIDDEN",
                    "message": "模型由服务端固定，不能通过请求覆盖",
                    "request_id": request.state.request_id,
                },
            )
        return JSONResponse(
            status_code=422,
            content={
                "code": "VALIDATION_ERROR",
                "message": "请求参数无效",
                "request_id": request.state.request_id,
            },
        )

    @app.get("/health")
    def health() -> dict[str, object]:
        report = _get_ai_health_report(app)
        return {"status": "ok", "ai_available": report.available}

    @app.get("/internal/health/ai")
    def ai_health() -> JSONResponse:
        """Operator health endpoint; it never returns endpoint URLs or credentials."""
        report = _get_ai_health_report(app)
        return JSONResponse(status_code=200 if report.available else 503, content=report.as_dict())

    @app.get("/health/ready")
    def readiness() -> JSONResponse:
        """Check deployed, non-model dependencies without exposing connection details."""
        from app.db.session import get_session_factory
        from app.integrations.object_storage import MinioObjectStorage
        from app.integrations.vector_store import PgVectorStore

        settings = get_settings()
        checks: dict[str, bool] = {}

        try:
            session = get_session_factory()()
            try:
                session.execute(text("SELECT 1"))
            finally:
                session.close()
            checks["postgres"] = True
        except Exception:
            checks["postgres"] = False

        try:
            if not settings.redis_url:
                raise RuntimeError("Redis is not configured")
            from redis import Redis

            Redis.from_url(settings.redis_url, socket_connect_timeout=3, socket_timeout=3).ping()
            checks["redis"] = True
        except Exception:
            checks["redis"] = False

        try:
            MinioObjectStorage(settings).check_available()
            checks["minio"] = True
        except Exception:
            checks["minio"] = False

        try:
            PgVectorStore(settings).check_available()
            checks["pgvector"] = True
        except Exception:
            checks["pgvector"] = False

        try:
            if not settings.mineru_base_url or not settings.mineru_api_key:
                raise RuntimeError("MinerU is not configured")
            import httpx

            response = httpx.post(
                f"{settings.mineru_base_url.rstrip('/')}/file-urls/batch",
                headers={"Authorization": f"Bearer {settings.mineru_api_key.get_secret_value()}"},
                # The v4 API validates the token before returning this known,
                # side-effect-free invalid-request response. Do not create an
                # upload URL merely to prove the service is reachable.
                json={"files": []},
                timeout=5,
            )
            response.raise_for_status()
            if response.json().get("code") != -10002:
                raise RuntimeError("MinerU authentication probe failed")
            checks["mineru"] = True
        except Exception:
            checks["mineru"] = False

        ready = all(checks.values())
        ai_report = _get_ai_health_report(app)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={
                "status": "ok" if ready else "degraded",
                "checks": checks,
                "ai_available": ai_report.available,
            },
        )

    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static", html=False),
        name="static",
    )

    @app.get("/WW_verify_WC6YuQxfn2lqhsXa.txt", include_in_schema=False)
    async def wecom_verify():
        from pathlib import Path

        f = Path(__file__).parent / "static" / "WW_verify_WC6YuQxfn2lqhsXa.txt"
        return PlainTextResponse(f.read_text().strip())

    app.include_router(api_router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
