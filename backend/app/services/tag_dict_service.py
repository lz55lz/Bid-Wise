"""标签字典服务 - L1/L2/L3 标签体系支撑

提供：
- 全量标签查询（分页/按分类/按优先级）
- 标签字典查询（用于 LLM extraction prompt 构造）
- 标签关系查询（CONSTRAINS/TRIGGERS 等）
- chunk → 候选标签召回（向量+关键词双层）
- Redis 缓存（1h TTL）
"""
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import BidTagCategory, BidTagDict, BidTagLevel, BidTagRelation
from app.db.session import get_session_factory

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        settings = get_settings()
        _redis = aioredis.from_url(
            settings.redis_url or "redis://localhost:6379/0",
            decode_responses=True,
        )
    return _redis


async def get_all_tags_cached() -> list[dict[str, Any]]:
    """全量标签（Redis 缓存 1h），返回 dict 列表"""
    r = await _get_redis()
    cached = await r.get("bid:tag_dict:all")
    if cached:
        return json.loads(cached)

    session = get_session_factory()()
    try:
        rows = session.query(BidTagDict).filter(BidTagDict.is_active == True).all()
        tags = [
            {
                "tag_id": t.tag_id,
                "tag_code": t.tag_code,
                "tag_name": t.tag_name,
                "category_code": t.category_code,
                "level_code": t.level_code,
                "data_type": t.data_type,
                "extraction_prompt": t.extraction_prompt,
                "value_example": t.value_example,
                "priority": t.priority,
            }
            for t in rows
        ]
        await r.setex("bid:tag_dict:all", 3600, json.dumps(tags, ensure_ascii=False))
        return tags
    finally:
        session.close()


async def get_p0_tags_cached() -> list[dict[str, Any]]:
    """P0 标签（从缓存过滤）"""
    all_tags = await get_all_tags_cached()
    return [t for t in all_tags if t.get("level_code") == "P0"]


async def invalidate_tag_cache() -> None:
    """失效标签缓存"""
    r = await _get_redis()
    await r.delete("bid:tag_dict:all")


def get_all_tags(session: Session) -> list[BidTagDict]:
    """全量标签（激活态，同步版）"""
    return session.query(BidTagDict).filter(BidTagDict.is_active == True).all()


def get_tags_by_category(session: Session, category_code: str) -> list[BidTagDict]:
    """按分类查标签"""
    return (
        session.query(BidTagDict)
        .filter(BidTagDict.category_code == category_code, BidTagDict.is_active == True)
        .all()
    )


def get_tags_by_level(session: Session, level_code: str) -> list[BidTagDict]:
    """按优先级查标签"""
    return (
        session.query(BidTagDict)
        .filter(BidTagDict.level_code == level_code, BidTagDict.is_active == True)
        .all()
    )


def get_p0_tags(session: Session) -> list[BidTagDict]:
    """P0 关键必填标签"""
    return get_tags_by_level(session, "P0")


def get_tag_dict_for_prompt(session: Session, tag_code: str) -> BidTagDict | None:
    """单个标签字典（含 extraction_prompt）"""
    return session.query(BidTagDict).filter(BidTagDict.tag_code == tag_code).first()


def get_all_categories(session: Session) -> list[BidTagCategory]:
    """全部分类"""
    return session.query(BidTagCategory).filter(BidTagCategory.is_active == True).order_by(BidTagCategory.sort_order).all()


def get_all_levels(session: Session) -> list[BidTagLevel]:
    """全部优先级"""
    return session.query(BidTagLevel).order_by(BidTagLevel.sort_order).all()


def get_tag_relations(session: Session, tag_code: str) -> list[BidTagRelation]:
    """某标签的所有关系（作为源或目标）"""
    return (
        session.query(BidTagRelation)
        .filter(
            (BidTagRelation.source_tag_code == tag_code) | (BidTagRelation.target_tag_code == tag_code),
            BidTagRelation.is_active == True,
        )
        .all()
    )


def get_triggered_tags(session: Session, tag_code: str) -> list[str]:
    """获取因某标签触发（TRIGGERS）的其他标签 code"""
    relations = (
        session.query(BidTagRelation.target_tag_code)
        .filter(
            BidTagRelation.source_tag_code == tag_code,
            BidTagRelation.relation_type == "TRIGGERS",
            BidTagRelation.is_active == True,
        )
        .all()
    )
    return [r[0] for r in relations]


def get_constrained_tags(session: Session, tag_code: str) -> list[BidTagRelation]:
    """获取约束某标签的其他标签（CONSTRAINS）"""
    return (
        session.query(BidTagRelation)
        .filter(
            BidTagRelation.target_tag_code == tag_code,
            BidTagRelation.relation_type == "CONSTRAINS",
            BidTagRelation.is_active == True,
        )
        .all()
    )


def get_tag_summary(session: Session) -> list[dict[str, Any]]:
    """汇总视图：每个分类+优先级的标签数量（用于报告）"""
    result = session.execute(
        text("SELECT * FROM v_tag_summary"),
    )
    rows = result.fetchall()
    cols = result.keys()
    return [dict(zip(cols, row)) for row in rows]


def search_tags_by_keyword(session: Session, keyword: str, limit: int = 20) -> list[BidTagDict]:
    """按标签名或 tag_code 模糊搜索（用于人工补充标签）"""
    pattern = f"%{keyword}%"
    return (
        session.query(BidTagDict)
        .filter(
            (BidTagDict.tag_name.ilike(pattern)) | (BidTagDict.tag_code.ilike(pattern)),
            BidTagDict.is_active == True,
        )
        .limit(limit)
        .all()
    )


def get_candidate_tags_for_chunk(
    session: Session, category_codes: list[str] | None = None, limit: int = 30
) -> list[BidTagDict]:
    """给定分类列表，召回候选标签（用于 L2 tagging 入口）"""
    q = session.query(BidTagDict).filter(BidTagDict.is_active == True)
    if category_codes:
        q = q.filter(BidTagDict.category_code.in_(category_codes))
    return q.limit(limit).all()


async def get_candidate_tags_for_chunk_async(
    session, category_codes: list[str] | None = None, limit: int = 30
) -> list[BidTagDict]:
    """异步版本：给定分类列表，召回候选标签"""
    from sqlalchemy import select
    q = select(BidTagDict).where(BidTagDict.is_active == True)
    if category_codes:
        q = q.where(BidTagDict.category_code.in_(category_codes))
    result = await session.execute(q.limit(limit))
    return list(result.scalars().all())
