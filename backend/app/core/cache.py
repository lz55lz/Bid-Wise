"""Redis 缓存工具。

提供 JSON 序列化的 get/set，支持 TTL。
"""
import hashlib
import json
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.core.config import get_settings

# TTL: 1 hour
_QUERY_CACHE_TTL = 3600


def _query_cache_key(query: str) -> str:
    """生成 query 缓存 key。"""
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
    return f"rewrite:v1:{h}"


def _get_client() -> Redis | None:
    """获取 Redis 客户端（lazy init）。"""
    try:
        settings = get_settings()
    except Exception:
        return None
    if not settings.redis_url:
        return None
    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        client.ping()
        return client
    except RedisError:
        return None


def cache_get(key: str) -> dict[str, Any] | None:
    """从 Redis 读取缓存，返回 dict 或 None。"""
    client = _get_client()
    if client is None:
        return None
    try:
        raw = client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except (RedisError, json.JSONDecodeError, TypeError):
        return None


def cache_set(key: str, value: dict[str, Any], ttl: int = _QUERY_CACHE_TTL) -> bool:
    """写入 Redis 缓存。失败返回 False，不影响主流程。"""
    client = _get_client()
    if client is None:
        return False
    try:
        client.setex(key, ttl, json.dumps(value, ensure_ascii=False))
        return True
    except (RedisError, TypeError):
        return False
