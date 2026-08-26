"""IM 适配器入口。"""

from app.integrations.im.adapters.base import IMAdapter
from app.integrations.im.adapters.feishu import REGION_FEISHU, REGION_LARK, FeishuAdapter
from app.integrations.im.adapters.wecom import WeComAdapter

__all__ = ["IMAdapter", "WeComAdapter", "FeishuAdapter", "REGION_FEISHU", "REGION_LARK"]
