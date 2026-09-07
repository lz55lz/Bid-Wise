"""密码与 JWT 的底层实现。

这里不查询数据库也不判断角色；令牌中的用户是否仍有效、角色是什么，必须由身份域
服务回查 PostgreSQL。这样禁用用户或调整角色会立刻在下一次请求生效。
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
from pwdlib import PasswordHash

from app.core.errors import DomainError

_password_hasher = PasswordHash.recommended()
_jwt_algorithm = "HS256"


def hash_password(password: str) -> str:
    """以 Argon2 安全哈希保存密码，绝不保存明文或可逆加密文本。"""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证密码；格式异常也视为验证失败，避免暴露账户内部状态。"""
    try:
        return _password_hasher.verify(password, password_hash)
    except ValueError:
        return False


def create_access_token(user_id: str, secret: str, expires_minutes: int) -> tuple[str, datetime]:
    """签发短期访问令牌；角色不写入令牌，避免权限变更后令牌陈旧。"""
    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=expires_minutes)
    token = jwt.encode(
        {"sub": user_id, "jti": str(uuid4()), "iat": now, "exp": expires_at},
        secret,
        algorithm=_jwt_algorithm,
    )
    return token, expires_at


def decode_access_token(token: str, secret: str) -> dict[str, object]:
    """仅负责验签与过期校验；调用方继续回查用户与授权记录。"""
    try:
        return jwt.decode(token, secret, algorithms=[_jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise DomainError("AUTHENTICATION_FAILED", "登录状态无效或已过期", 401) from exc
