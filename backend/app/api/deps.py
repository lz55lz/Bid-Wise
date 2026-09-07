"""跨领域 HTTP 依赖项：只构造可信调用上下文，不承载业务决策。"""

from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.db.session import get_db_session
from app.modules.identity.service import AuthenticatedUser, AuthService

_bearer_scheme = HTTPBearer(auto_error=False)

DatabaseSession = Annotated[AsyncSession, Depends(get_db_session)]
ApplicationSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    session: DatabaseSession,
    settings: ApplicationSettings,
) -> AuthenticatedUser:
    """把 Bearer Token 解析为已回查数据库的可信用户，不接受前端角色声明。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
    return await AuthService(session, settings).resolve(credentials.credentials)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


async def get_access_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> str:
    """提取已声明为 Bearer 的原始令牌，专供登出等需要撤销当前令牌的用例。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
    return credentials.credentials


AccessToken = Annotated[str, Depends(get_access_token)]
