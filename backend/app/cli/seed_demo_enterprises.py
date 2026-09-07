"""创建可用于项目匹配的演示企业和已确认材料。

仅新增带 ``demo_seed`` 标记的记录；重复执行不会覆盖用户维护的企业或材料。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from app.db.session import dispose_database_engine, get_session_factory
from app.modules.identity.models import User, UserSystemRole
from app.modules.identity.service import AuthenticatedUser
from app.modules.materials.models import EnterpriseMaterial
from app.modules.materials.schemas import MaterialUpsertRequest
from app.modules.materials.service import MaterialService
from app.modules.projects.enterprise_schemas import EnterpriseCreateRequest
from app.modules.projects.enterprise_service import EnterpriseService
from app.modules.projects.models import Enterprise

_VALID_TO = date(2029, 12, 31)


@dataclass(frozen=True, slots=True)
class DemoEnterprise:
    name: str
    credit_code: str
    enterprise_type: str
    qualifications: str
    experience: str


_ENTERPRISES = (
    DemoEnterprise(
        "演示数据·华东建工集团有限公司",
        "91310000DEMO000001",
        "工程施工",
        "建筑工程施工总承包一级、市政公用工程施工总承包一级",
        "城市综合管廊及轨道交通配套工程",
    ),
    DemoEnterprise(
        "演示数据·智联数字科技有限公司",
        "91310000DEMO000002",
        "信息化服务",
        "电子与智能化工程专业承包一级、信息系统建设和服务能力 CS4",
        "政务云、智慧园区及数据中心集成项目",
    ),
    DemoEnterprise(
        "演示数据·绿源环保工程有限公司",
        "91310000DEMO000003",
        "环保工程",
        "环保工程专业承包一级、环境工程专项设计甲级",
        "工业园区污水处理及固废处置项目",
    ),
    DemoEnterprise(
        "演示数据·安信安全技术服务有限公司",
        "91310000DEMO000004",
        "安全技术服务",
        "安全评价机构资质、消防设施工程专业承包二级",
        "危化企业安全评价及消防改造项目",
    ),
    DemoEnterprise(
        "演示数据·衡达工程咨询有限公司",
        "91310000DEMO000005",
        "工程咨询",
        "工程咨询单位甲级资信、工程造价咨询甲级",
        "政府投资项目全过程咨询及造价控制项目",
    ),
    DemoEnterprise(
        "演示数据·精工装备制造有限公司",
        "91310000DEMO000006",
        "设备制造",
        "质量管理体系认证、特种设备生产许可证",
        "大型机电设备供货、安装及运维项目",
    ),
)


def _materials(item: DemoEnterprise) -> tuple[dict[str, object], ...]:
    """为每家演示企业提供匹配 DSL 可直接识别的完整材料集合。"""
    prefix = item.credit_code[-6:]
    return (
        {
            "material_type": "QUALIFICATION",
            "name": f"{item.qualifications}及营业执照",
            "material_no": f"DEMO-QUAL-{prefix}",
            "issuer": "相关主管部门",
            "level": "一级/甲级",
            "valid_from": date(2023, 1, 1),
            "valid_to": _VALID_TO,
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_BUSINESS_LICENSE", "QUAL_QUALIFICATION"],
            },
        },
        {
            "material_type": "CERTIFICATE",
            "name": "安全生产、信用与保险证明",
            "material_no": f"DEMO-CERT-{prefix}",
            "issuer": "认证机构及保险机构",
            "level": "有效",
            "valid_from": date(2024, 1, 1),
            "valid_to": _VALID_TO,
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_SAFETY", "QUAL_CREDIT", "QUAL_INSURANCE"],
            },
        },
        {
            "material_type": "PERSONNEL",
            "name": "项目经理、技术负责人及安全管理人员配置",
            "material_no": f"DEMO-STAFF-{prefix}",
            "issuer": "人力资源部门",
            "level": "高级",
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_PERSONNEL"],
                "persons": [
                    {"role": "项目经理", "certificate": "一级注册建造师", "experience_years": 12},
                    {"role": "技术负责人", "certificate": "高级工程师", "experience_years": 15},
                    {
                        "role": "安全负责人",
                        "certificate": "安全生产考核合格证",
                        "experience_years": 9,
                    },
                ],
            },
        },
        {
            "material_type": "PROJECT_EXPERIENCE",
            "name": f"近三年{item.experience}业绩",
            "material_no": f"DEMO-EXP-{prefix}",
            "issuer": "项目业主单位",
            "amount": "68000000.00",
            "currency": "CNY",
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_SIMILAR_EXPERIENCE"],
                "project_count": 5,
                "recent_years": 3,
            },
        },
        {
            "material_type": "FINANCE",
            "name": "近三年审计报告、完税及注册资本证明",
            "material_no": f"DEMO-FIN-{prefix}",
            "issuer": "会计师事务所",
            "amount": "180000000.00",
            "currency": "CNY",
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_FINANCIAL", "QUAL_REGISTERED_CAPITAL", "QUAL_TAX"],
                "annual_revenue": "680000000.00",
                "debt_ratio": "0.42",
                "tax_credit": "A",
            },
        },
        {
            "material_type": "OTHER",
            "name": "诚信承诺、无失信记录及主要设备清单",
            "material_no": f"DEMO-OTHER-{prefix}",
            "issuer": "企业及信用信息平台",
            "attributes": {
                "demo_seed": True,
                "tag_codes": ["QUAL_BLACKLIST", "QUAL_EQUIPMENT"],
                "no_blacklist_record": True,
                "equipment": ["施工机械", "检测设备", "安全防护设备"],
            },
        },
        {
            "material_type": "CERTIFICATE",
            "name": "质量、环境与职业健康安全管理体系认证",
            "material_no": f"DEMO-ISO-{prefix}",
            "issuer": "认证机构",
            "level": "ISO 9001/14001/45001",
            "valid_from": date(2024, 6, 1),
            "valid_to": _VALID_TO,
            "attributes": {"demo_seed": True, "tag_codes": ["QUAL_CREDIT", "QUAL_SAFETY"]},
        },
        {
            "material_type": "OTHER",
            "name": "联合体投标授权与履约保障材料",
            "material_no": f"DEMO-JOINT-{prefix}",
            "issuer": "企业法务部门",
            "attributes": {"demo_seed": True, "tag_codes": ["QUAL_JOINT_BID"]},
        },
    )


async def _get_seed_actor() -> AuthenticatedUser:
    async with get_session_factory()() as session:
        statement = (
            select(User)
            .join(UserSystemRole, UserSystemRole.user_id == User.id)
            .where(User.status == "ACTIVE", UserSystemRole.role_code == "SYSTEM_ADMIN")
            .order_by(User.created_at)
        )
        user = await session.scalar(statement)
        if user is None:
            raise RuntimeError("未找到启用的 SYSTEM_ADMIN，无法创建演示企业数据")
        role_statement = select(UserSystemRole.role_code).where(
            UserSystemRole.user_id == user.id
        )
        roles = set((await session.scalars(role_statement)).all())
        return AuthenticatedUser(user.id, user.username, user.display_name, frozenset(roles))


async def seed() -> tuple[int, int]:
    actor = await _get_seed_actor()
    enterprise_count = 0
    material_count = 0
    async with get_session_factory()() as session:
        enterprises = EnterpriseService(session)
        materials = MaterialService(session)
        for definition in _ENTERPRISES:
            enterprise = await session.scalar(
                select(Enterprise).where(Enterprise.credit_code == definition.credit_code)
            )
            if enterprise is None:
                created = await enterprises.create(
                    actor,
                    EnterpriseCreateRequest(
                        name=definition.name,
                        credit_code=definition.credit_code,
                        enterprise_type=definition.enterprise_type,
                    ),
                )
                enterprise = await session.get(Enterprise, created.id)
                enterprise_count += 1
            if enterprise is None:
                raise RuntimeError("演示企业创建后未找到记录")

            for material in _materials(definition):
                exists = await session.scalar(
                    select(EnterpriseMaterial.id).where(
                        EnterpriseMaterial.material_no == material["material_no"],
                        EnterpriseMaterial.deleted_at.is_(None),
                    )
                )
                if exists is not None:
                    continue
                created_material = await materials.create(
                    actor,
                    MaterialUpsertRequest(enterprise_id=enterprise.id, **material),
                )
                await materials.confirm(created_material.id, actor)
                material_count += 1
        await session.commit()
    return enterprise_count, material_count


async def main() -> None:
    try:
        enterprise_count, material_count = await seed()
        print(f"新增演示企业 {enterprise_count} 家，新增已确认材料 {material_count} 条。")
    finally:
        await dispose_database_engine()


if __name__ == "__main__":
    asyncio.run(main())
