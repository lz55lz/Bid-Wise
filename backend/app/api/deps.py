from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.core.security import decode_access_token
from app.db.repositories.identity_repository import IdentityRepository
from app.db.session import get_db_session
from app.integrations.object_storage import MinioObjectStorage, ObjectStorageUnavailable
from app.integrations.task_publisher import ArqTaskPublisher
from app.services.document_service import DocumentService

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    id: UUID
    username: str
    display_name: str
    role_codes: set[str]
    token_id: str


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> CurrentUser:
    if credentials is None or credentials.scheme.lower() != "bearer" or not settings.jwt_secret_key:
        raise DomainError("AUTHENTICATION_FAILED", "未登录或登录状态已失效", 401)
    payload = decode_access_token(
        credentials.credentials, settings.jwt_secret_key.get_secret_value()
    )
    try:
        user_id = UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401) from exc
    user = IdentityRepository(session).get_user(user_id)
    if user is None:
        raise DomainError("AUTHENTICATION_FAILED", "用户不存在或已禁用", 401)
    return CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role_codes=IdentityRepository(session).list_role_codes(user.id),
        token_id=payload["jti"],
    )


def get_document_service(
    session: Session = Depends(get_db_session), settings: Settings = Depends(get_settings)
) -> DocumentService:
    try:
        return DocumentService(
            session,
            MinioObjectStorage(settings),
            ArqTaskPublisher(),
        )
    except ObjectStorageUnavailable as exc:
        raise DomainError("OBJECT_STORAGE_UNAVAILABLE", "对象存储暂不可用", 503) from exc
