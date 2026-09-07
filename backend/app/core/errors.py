"""面向 HTTP 的统一业务异常与响应格式。"""

import logging

from fastapi import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class DomainError(Exception):
    """服务层主动抛出的、可安全返回给调用方的业务异常。

    Repository 只做数据读写，不应抛出这类包含业务语义的异常；服务层根据
    用例把持久化结果转换为错误码，再由本模块统一序列化为 HTTP 响应。
    """

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        super().__init__(message)


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """避免各个路由重复手写错误 JSON，且不泄漏内部堆栈。"""
    if not isinstance(exc, DomainError):  # 仅供类型收窄；注册时只会传入 DomainError。
        raise exc
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": getattr(request.state, "request_id", None),
        },
    )


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """未知异常只记录在服务端日志，客户端获得稳定而不暴露细节的响应。"""
    logger.exception(
        "Unhandled application exception",
        exc_info=exc,
        extra={"request_id": getattr(request.state, "request_id", None)},
    )
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "服务暂时不可用，请稍后重试",
            "request_id": getattr(request.state, "request_id", None),
        },
    )
