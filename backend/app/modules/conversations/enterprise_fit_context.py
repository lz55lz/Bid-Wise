"""面向项目问答的绑定企业适配度摘要。"""

from collections import Counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.matching.models import MaterialMatchResult
from app.modules.materials.models import EnterpriseMaterial
from app.modules.projects.models import Enterprise, ProjectEnterprise
from app.modules.requirements.repository import RequirementRepository

_STATUS_LABELS = {"MATCHED": "匹配", "UNCERTAIN": "待补充", "MISSING": "缺失"}


class EnterpriseFitContextService:
    """将绑定企业与匹配结果压缩为可直接回答的、无模型参与的上下文。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._requirements = RequirementRepository(session)

    async def build(self, project_id: UUID) -> dict[str, object]:
        enterprise_rows = (
            await self._session.execute(
                select(Enterprise, ProjectEnterprise.is_lead)
                .join(ProjectEnterprise, ProjectEnterprise.enterprise_id == Enterprise.id)
                .where(ProjectEnterprise.project_id == project_id, Enterprise.deleted_at.is_(None))
                .order_by(ProjectEnterprise.is_lead.desc(), Enterprise.name)
            )
        ).all()
        enterprises = {
            enterprise.id: (enterprise.name, is_lead) for enterprise, is_lead in enterprise_rows
        }
        if not enterprises:
            return {"content": "当前项目尚未绑定企业，无法计算企业适配度。"}

        materials = list(
            (
                await self._session.scalars(
                    select(EnterpriseMaterial).where(
                        EnterpriseMaterial.enterprise_id.in_(enterprises),
                        EnterpriseMaterial.status == "CONFIRMED",
                        EnterpriseMaterial.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        material_by_id = {material.id: material for material in materials}
        material_counts = Counter(material.enterprise_id for material in materials)
        requirements = {
            item.id: item for item in await self._requirements.list_confirmed(project_id)
        }
        matches = list(
            (
                await self._session.scalars(
                    select(MaterialMatchResult).where(MaterialMatchResult.project_id == project_id)
                )
            ).all()
        )

        lines = ["当前绑定企业："]
        for enterprise_id, (name, is_lead) in enterprises.items():
            lead = "（牵头方）" if is_lead else ""
            lines.append(f"- {name}{lead}：已确认材料 {material_counts[enterprise_id]} 份")
        if not matches:
            lines.append("尚未生成企业材料匹配结果；目前不能判断整体适配度，请先执行匹配分析。")
            return {"content": "\n".join(lines)}

        counts = Counter(match.final_status for match in matches)
        lines.append(
            "匹配概览：已匹配 {matched} 项，待补充 {uncertain} 项，缺失 {missing} 项。".format(
                matched=counts["MATCHED"], uncertain=counts["UNCERTAIN"], missing=counts["MISSING"]
            )
        )
        lines.append("优先处理项：")
        ordered = sorted(
            matches,
            key=lambda item: (
                {"MISSING": 0, "UNCERTAIN": 1, "MATCHED": 2}.get(item.final_status, 3),
                item.id,
            ),
        )
        for match in ordered[:12]:
            requirement = requirements.get(match.requirement_id)
            material = material_by_id.get(match.material_id)
            enterprise_name = (
                enterprises.get(material.enterprise_id, ("未关联企业", False))[0]
                if material
                else "未匹配材料"
            )
            requirement_title = requirement.title if requirement else "已失效或未确认的要求"
            missing = "；".join(str(item) for item in match.missing_conditions[:3])
            detail = missing or match.reason
            lines.append(
                f"- [{_STATUS_LABELS.get(match.final_status, match.final_status)}] "
                f"{requirement_title}"
                f"｜企业：{enterprise_name}｜材料：{material.name if material else '无'}｜{detail}"
            )
        return {"content": "\n".join(lines)}
