"""标签库查询用例：浏览器只读取数据库基线，不维护另一份字典。"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tender_analysis.models import TenderTag
from app.modules.tender_analysis.schemas import TenderTagCatalogItem


class TenderTagCatalogService:
    """为审核页和管理页提供一致的活动标签目录。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_active(
        self, category_code: str | None, level_code: str | None
    ) -> list[TenderTagCatalogItem]:
        statement = select(TenderTag).where(TenderTag.is_active.is_(True))
        if category_code is not None:
            statement = statement.where(TenderTag.category_code == category_code)
        if level_code is not None:
            statement = statement.where(TenderTag.level_code == level_code)
        statement = statement.order_by(
            TenderTag.category_code,
            TenderTag.level_code,
            TenderTag.code,
        )
        return [
            TenderTagCatalogItem.model_validate(item, from_attributes=True)
            for item in (await self._session.scalars(statement)).all()
        ]

    async def set_active(self, code: str, is_active: bool, role_codes: frozenset[str]) -> None:
        """只允许系统管理员启停标签，避免项目成员改变全局抽取口径。"""
        from app.core.errors import DomainError

        if "SYSTEM_ADMIN" not in role_codes:
            raise DomainError("PERMISSION_DENIED", "无权维护标签库", 403)
        tag = await self._session.scalar(
            select(TenderTag).where(TenderTag.code == code).with_for_update()
        )
        if tag is None:
            raise DomainError("RESOURCE_NOT_FOUND", "标签不存在", 404)
        tag.is_active = is_active
