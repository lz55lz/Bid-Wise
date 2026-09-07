"""投标分析图的依赖装配。

Worker 只负责运行/恢复持久化任务，不应该分别维护两套 LangGraph 依赖组装代码。
本模块集中创建一次请求范围内的节点与服务；Session 仍由 Worker 生命周期管理。
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.modules.tender_analysis.completion_service import TenderPipelineCompletionService
from app.modules.tender_analysis.extraction_service import TenderExtractionService
from app.modules.tender_analysis.pipeline_nodes import TenderPipelineNodes
from app.modules.tender_analysis.workflow import build_tender_pipeline_graph


def build_tender_pipeline(session: AsyncSession, settings: Settings):
    """构造一条共享同一短生命周期 Session 的投标分析图。"""
    nodes = TenderPipelineNodes(session)
    return build_tender_pipeline_graph(
        preflight=nodes.preflight,
        select_candidates=nodes.select_candidates,
        extract=TenderExtractionService(session, settings).extract,
        validate=nodes.validate,
        finalize=TenderPipelineCompletionService(session).finalize,
        on_node_error=nodes.mark_stage_failed,
    )
