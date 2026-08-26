from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.errors import DomainError

PASSWORD_HASHER = PasswordHash.recommended()
REVOKED_TOKEN_IDS: set[str] = set()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return PASSWORD_HASHER.verify(password, password_hash)


def create_access_token(
    user_id: str, secret: str, expires_minutes: int
) -> tuple[str, str, datetime]:
    expires_at = datetime.now(UTC) + timedelta(minutes=expires_minutes)
    token_id = str(uuid4())
    token = jwt.encode(
        {"sub": user_id, "jti": token_id, "exp": expires_at, "iat": datetime.now(UTC)},
        secret,
        algorithm="HS256",
    )
    return token, token_id, expires_at


def decode_access_token(token: str, secret: str) -> dict[str, str]:
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
        if payload.get("jti") in REVOKED_TOKEN_IDS:
            raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401)
        return payload
    except jwt.PyJWTError as exc:
        raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401) from exc


def revoke_access_token(token_id: str) -> None:
    REVOKED_TOKEN_IDS.add(token_id)
