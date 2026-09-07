"""统一登记 v1 领域路由，main.py 不直接堆放业务接口。"""

from fastapi import APIRouter

from app.api.v1.admin_users import router as admin_users_router
from app.api.v1.analysis import router as analysis_router
from app.api.v1.audits import router as audits_router
from app.api.v1.auth import router as auth_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.global_conversations import router as global_conversations_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.decisions import router as decisions_router
from app.api.v1.documents import router as documents_router
from app.api.v1.enterprises import router as enterprises_router
from app.api.v1.evaluations import router as evaluations_router
from app.api.v1.evidences import router as evidences_router
from app.api.v1.findings import router as findings_router
from app.api.v1.full_analysis import router as full_analysis_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.matches import router as matches_router
from app.api.v1.materials import router as materials_router
from app.api.v1.memories import router as memories_router
from app.api.v1.projects import router as projects_router
from app.api.v1.rag import router as rag_router
from app.api.v1.reports import router as reports_router
from app.api.v1.requirements import router as requirements_router
from app.api.v1.risk_rule_templates import router as risk_rule_templates_router
from app.api.v1.risks import router as risks_router
from app.api.v1.tender_analysis import router as tender_analysis_router
from app.api.v1.tender_analysis import tag_catalog_router

router = APIRouter(prefix="/api/v1")
router.include_router(admin_users_router)
router.include_router(analysis_router)
router.include_router(audits_router)
router.include_router(auth_router)
router.include_router(conversations_router)
router.include_router(global_conversations_router)
router.include_router(documents_router)
router.include_router(enterprises_router)
router.include_router(decisions_router)
router.include_router(dashboard_router)
router.include_router(evidences_router)
router.include_router(evaluations_router)
router.include_router(findings_router)
router.include_router(full_analysis_router)
router.include_router(knowledge_router)
router.include_router(materials_router)
router.include_router(memories_router)
router.include_router(matches_router)
router.include_router(projects_router)
router.include_router(rag_router)
router.include_router(reports_router)
router.include_router(requirements_router)
router.include_router(risks_router)
router.include_router(risk_rule_templates_router)
router.include_router(tender_analysis_router)
router.include_router(tag_catalog_router)

# 项目、文档、知识库等领域在完成迁移后只需在此处注册自己的 router。
