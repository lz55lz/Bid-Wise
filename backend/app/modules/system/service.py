"""基础依赖就绪检查。"""

import logging

from sqlalchemy import text

from app.core.config import Settings
from app.db.session import get_engine
from app.integrations.redis_pool import borrow_redis_pool, invalidate_redis_pool

logger = logging.getLogger(__name__)


class DependencyUnavailable(Exception):
    """数据库或 Redis 不能响应轻量探针。"""


class ReadinessService:
    """只检查服务运行所必需的依赖，不执行迁移、不创建桶、不调用 AI。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def check(self) -> None:
        """依次验证 PostgreSQL 和 Redis；任一失败即拒绝就绪。"""
        try:
            async with get_engine().connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("数据库就绪检查失败：%s", exc)
            raise DependencyUnavailable("数据库不可用") from exc
        try:
            async with borrow_redis_pool(self._settings.redis_url) as pool:
                await pool.ping()
        except Exception as exc:
            await invalidate_redis_pool()
            logger.warning("Redis 就绪检查失败：%s", exc)
            raise DependencyUnavailable("Redis 不可用") from exc
