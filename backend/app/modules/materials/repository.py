"""企业材料持久化访问。"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.materials.models import EnterpriseMaterial


class MaterialRepository:
    """仓储只读写材料事实，不决定确认条件和角色权限。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_material(self, material: EnterpriseMaterial) -> None:
        self._session.add(material)

    async def get_material(
        self, material_id: UUID, *, for_update: bool = False
    ) -> EnterpriseMaterial | None:
        statement = select(EnterpriseMaterial).where(
            EnterpriseMaterial.id == material_id, EnterpriseMaterial.deleted_at.is_(None)
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_materials(self, enterprise_id: UUID | None = None) -> list[EnterpriseMaterial]:
        statement = select(EnterpriseMaterial).where(EnterpriseMaterial.deleted_at.is_(None))
        if enterprise_id is not None:
            statement = statement.where(EnterpriseMaterial.enterprise_id == enterprise_id)
        statement = statement.order_by(
            EnterpriseMaterial.material_type,
            EnterpriseMaterial.name,
            EnterpriseMaterial.id,
        )
        return list((await self._session.scalars(statement)).all())
