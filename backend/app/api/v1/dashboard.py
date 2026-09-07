"""工作台聚合接口。"""

from fastapi import APIRouter

from app.api.deps import CurrentUser, DatabaseSession
from app.modules.projects.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["工作台"])


@router.get("")
async def get_dashboard(current_user: CurrentUser, session: DatabaseSession) -> dict[str, object]:
    return await DashboardService(session).summary(current_user)
