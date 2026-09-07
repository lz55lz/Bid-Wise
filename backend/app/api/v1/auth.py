"""身份认证 HTTP 接口。"""

from fastapi import APIRouter, Request, Response, status

from app.api.deps import AccessToken, ApplicationSettings, CurrentUser, DatabaseSession
from app.core.errors import DomainError
from app.integrations.login_rate_limit import LoginRateLimiter
from app.modules.identity.schemas import CurrentUserResponse, LoginRequest, LoginResponse
from app.modules.identity.service import AuthService

router = APIRouter(prefix="/auth", tags=["身份认证"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: Request,
    payload: LoginRequest,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> LoginResponse:
    """使用内部账号登录；失败按 IP + 用户名做短期限流且不泄漏账户存在性。"""
    ip = request.client.host if request.client is not None else "unknown"
    limiter = LoginRateLimiter(settings)
    await limiter.check(ip, payload.username)
    try:
        response = await AuthService(session, settings).login(payload.username, payload.password)
    except DomainError as exc:
        if exc.code == "AUTHENTICATION_FAILED":
            await limiter.record_failure(ip, payload.username)
        raise
    await limiter.clear_identity(ip, payload.username)
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(current_user: CurrentUser) -> CurrentUserResponse:
    """返回已通过令牌和数据库回查验证的当前身份。"""
    return CurrentUserResponse(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        roles=sorted(current_user.role_codes),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    access_token: AccessToken,
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> Response:
    """撤销当前 Bearer Token；令牌的完整内容不会写入 PostgreSQL。"""
    await AuthService(session, settings).logout(access_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
