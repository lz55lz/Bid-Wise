"""登录失败限流；Redis 只保存短期计数，不保存用户名明文或密码。"""

import hashlib

from app.core.config import Settings
from app.core.errors import DomainError
from app.integrations.redis_pool import borrow_redis_pool, invalidate_redis_pool

_WINDOW_SECONDS = 5 * 60
_MAX_USER_IP_FAILURES = 10
_MAX_IP_FAILURES = 30


class LoginRateLimiter:
    def __init__(self, settings: Settings) -> None:
        self._redis_url = settings.redis_url

    @staticmethod
    def _identity_key(ip: str, username: str) -> str:
        digest = hashlib.sha256(f"{ip}|{username.lower()}".encode()).hexdigest()
        return f"auth:login:user-ip:{digest}"

    @staticmethod
    def _ip_key(ip: str) -> str:
        digest = hashlib.sha256(ip.encode()).hexdigest()
        return f"auth:login:ip:{digest}"

    async def check(self, ip: str, username: str) -> None:
        try:
            async with borrow_redis_pool(self._redis_url) as pool:
                user_ip, ip_count = await pool.mget(
                    self._identity_key(ip, username), self._ip_key(ip)
                )
        except Exception as exc:
            await invalidate_redis_pool()
            raise DomainError("AUTH_RATE_LIMIT_UNAVAILABLE", "认证服务暂不可用", 503) from exc
        if int(user_ip or 0) >= _MAX_USER_IP_FAILURES or int(ip_count or 0) >= _MAX_IP_FAILURES:
            raise DomainError("TOO_MANY_LOGIN_ATTEMPTS", "登录失败次数过多，请稍后再试", 429)

    async def record_failure(self, ip: str, username: str) -> None:
        script = """
        local a = redis.call('INCR', KEYS[1])
        if a == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
        local b = redis.call('INCR', KEYS[2])
        if b == 1 then redis.call('EXPIRE', KEYS[2], ARGV[1]) end
        return {a, b}
        """
        try:
            async with borrow_redis_pool(self._redis_url) as pool:
                await pool.eval(
                    script,
                    2,
                    self._identity_key(ip, username),
                    self._ip_key(ip),
                    _WINDOW_SECONDS,
                )
        except Exception:
            await invalidate_redis_pool()
            # 登录已经失败；限流记账故障不能把原始 401 变成账户枚举信号。
            return

    async def clear_identity(self, ip: str, username: str) -> None:
        try:
            async with borrow_redis_pool(self._redis_url) as pool:
                await pool.delete(self._identity_key(ip, username))
        except Exception:
            await invalidate_redis_pool()
            return
