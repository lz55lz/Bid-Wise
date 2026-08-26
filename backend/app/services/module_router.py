"""
模块化路由层：按章节内容将文档片段路由到专业抽取模块。

参考 tender-extract/module_router.py，按关键词将节点分发到专业模块，
每个模块独立 prompt 抽取本模块负责的字段，提高复杂条款的提取质量。
"""
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ModuleDef:
    """模块定义"""
    module_id: str
    name: str
    description: str
    # 路由关键词（命中越多得分越高）
    keywords: list[str]
    # 该模块负责抽取的字段（与 ProjectFieldCandidate.field_code 对应）
    target_fields: list[str]
    priority: int = 5  # 数字越小优先级越高


# =============================================================================
# 模块定义
# =============================================================================

MODULES: list[ModuleDef] = [
    ModuleDef(
        module_id="basic_info",
        name="基础信息",
        description="项目基本信息、名称、编号、地点、工期",
        keywords=[
            "项目名称", "项目编号", "招标编号", "工程名称",
            "项目概况", "招标公告", "招标范围", "项目背景",
            "建设地点", "工程地点", "项目地址", "建设规模",
            "工期", "工期要求", "计划工期", "合同工期",
            "工程地点", "项目位置", "地址",
        ],
        target_fields=["PROJECT_NAME", "PROJECT_CODE", "LOCATION"],
        priority=1,
    ),
    ModuleDef(
        module_id="financial",
        name="财务信息",
        description="投标报价、预算金额、保证金、限价",
        keywords=[
            "投标报价", "投标金额", "投标总报价", "报价",
            "投标保证金", "履约保证金", "保证金",
            "招标控制价", "预算金额", "项目金额", "合同金额",
            "人民币", "万元", "元整", "大写", "小写", "金额",
            "最高限价", "控制价", "限价",
        ],
        target_fields=["BID_BOND", "BUDGET", "MAX_PRICE"],
        priority=2,
    ),
    ModuleDef(
        module_id="qualification",
        name="资格要求",
        description="投标人资质、营业执照、证书要求",
        keywords=[
            "资格条件", "资质要求", "投标人资格",
            "营业执照", "统一社会信用代码", "注册资本",
            "成立日期", "经营范围", "业务范围",
            "资质证书", "资格证书", "等级证书",
            "资质等级", "施工资质", "设计资质",
            "安全生产许可证", "质量管理体系",
            "ISO", "认证", "资格",
        ],
        target_fields=["PURCHASER", "AGENCY"],
        priority=3,
    ),
    ModuleDef(
        module_id="evaluation",
        name="评标办法",
        description="评标方法、评分标准、评审规则",
        keywords=[
            "评标办法", "评标方法", "评审办法",
            "评分标准", "评标标准", "评审标准",
            "评分项", "分值", "权重", "得分",
            "技术评分", "商务评分", "综合评分",
            "最低评标价", "综合评估法", "经评审的最低投标价法",
            "评标委员会", "评标专家", "评标",
        ],
        target_fields=["EVALUATION_METHOD", "PROCUREMENT_METHOD"],
        priority=3,
    ),
    ModuleDef(
        module_id="submission",
        name="投标递交",
        description="投标截止时间、开标时间、有效期",
        keywords=[
            "投标文件", "递交", "投标截止",
            "开标时间", "开标地点", "投标截止时间",
            "投标有效期", "响应文件", "递交截止",
            "投标日期", "投标时间", "截止时间",
            "投标截止日期", "截止日期",
        ],
        target_fields=["BID_DEADLINE", "BID_OPENING_AT"],
        priority=2,
    ),
    ModuleDef(
        module_id="commercial",
        name="商务条款",
        description="付款方式、质保期、履约保证金",
        keywords=[
            "付款方式", "付款条件", "支付方式",
            "质保期", "质量保证期", "保修期",
            "履约保证金", "履约担保", "履约",
            "合同工期", "交货期", "供货期",
            "验收", "交货", "交付",
        ],
        target_fields=[],
        priority=4,
    ),
    ModuleDef(
        module_id="technical",
        name="技术要求",
        description="技术参数、标准、规格要求",
        keywords=[
            "技术要求", "技术标准", "技术参数", "规格要求",
            "技术规范", "技术方案", "技术指标",
            "★", "▲", "☆",
        ],
        target_fields=[],
        priority=4,
    ),
]


# =============================================================================
# 通用模块（未匹配到任何专业模块的节点）
# =============================================================================
GENERAL_MODULE = ModuleDef(
    module_id="general",
    name="通用抽取",
    description="未匹配专业模块的节点，兜底抽取",
    keywords=[],
    target_fields=[],  # 通用模块只抽取 requirement，不负责具体字段
    priority=9,
)


# =============================================================================
# 节点路由结果
# =============================================================================

@dataclass
class RoutedNode:
    """节点路由结果"""
    node: dict  # 原始节点 dict（包含 id, content, page_number）
    module_id: str
    module_name: str
    match_score: float
    matched_keywords: list[str] = field(default_factory=list)


# =============================================================================
# 路由器
# =============================================================================

class ModuleRouter:
    """
    节点路由器：按关键词将文档节点路由到专业模块。

    设计原则：
    - 一个节点可路由到多个模块（内容跨模块时取得分最高的前2个）
    - 未匹配任何模块的节点进入 general 模块
    - 按得分排序，同分时按模块 priority 排序
    """

    def __init__(self, modules: list[ModuleDef] | None = None) -> None:
        self._modules = modules or MODULES
        self._build_keyword_index()

    def _build_keyword_index(self) -> None:
        """构建关键词 → 模块 ID 列表的倒排索引"""
        self._keyword_to_modules: dict[str, list[str]] = {}
        for module in self._modules:
            for keyword in module.keywords:
                if keyword not in self._keyword_to_modules:
                    self._keyword_to_modules[keyword] = []
                self._keyword_to_modules[keyword].append(module.module_id)

    def route(self, nodes: list[dict]) -> list[RoutedNode]:
        """
        将节点列表路由到对应模块。

        Args:
            nodes: list[dict]，每个 dict 包含 id, content, page_number

        Returns:
            按 match_score 降序排列的 RoutedNode 列表
        """
        results: list[RoutedNode] = []
        for node in nodes:
            content = (node.get("content") or "") + " " + (node.get("title") or "")
            routes = self._route_single(content, node)
            if routes:
                # 只保留得分最高的模块，避免同一节点被多模块重复抽取
                results.append(routes[0])
            else:
                results.append(RoutedNode(
                    node=node,
                    module_id=GENERAL_MODULE.module_id,
                    module_name=GENERAL_MODULE.name,
                    match_score=0.0,
                    matched_keywords=[],
                ))
        logger.info(f"[ModuleRouter] 路由完成: {len(nodes)} 节点 → {len(results)} 个路由任务")
        return results

    def _route_single(self, text: str, node: dict) -> list[RoutedNode]:
        """对单个节点进行路由，返回匹配的模块列表（按得分降序）"""
        module_scores: dict[str, tuple[float, list[str]]] = {}

        for keyword, module_ids in self._keyword_to_modules.items():
            count = text.count(keyword)
            if count == 0:
                continue
            # 关键词越长越精确，权重越高
            weight = min(len(keyword) / 4.0, 3.0)
            score = count * weight
            for mid in module_ids:
                cur_score, cur_kws = module_scores.get(mid, (0.0, []))
                new_kws = cur_kws + [keyword]
                module_scores[mid] = (cur_score + score, new_kws)

        if not module_scores:
            return []

        # 取得分超过最高分 50% 的模块
        max_score = max(s for s, _ in module_scores.values())
        threshold = max_score * 0.5

        routes: list[RoutedNode] = []
        for module_id, (score, matched_kws) in module_scores.items():
            if score < threshold:
                continue
            module = self._get_module(module_id)
            if module:
                routes.append(RoutedNode(
                    node=node,
                    module_id=module.module_id,
                    module_name=module.name,
                    match_score=score,
                    matched_keywords=matched_kws,
                ))

        # 按得分降序，相同得分按 priority 升序
        routes.sort(key=lambda r: (-r.match_score, self._get_module(r.module_id).priority if self._get_module(r.module_id) else 9))
        return routes

    def _get_module(self, module_id: str) -> ModuleDef | None:
        for m in self._modules:
            if m.module_id == module_id:
                return m
        return None

    def get_module_target_fields(self, module_id: str) -> list[str]:
        """获取模块负责的字段列表"""
        module = self._get_module(module_id)
        return module.target_fields if module else []

    def route_stats(self, routed: list[RoutedNode]) -> dict:
        """获取路由统计摘要"""
        counts: dict[str, int] = {}
        for r in routed:
            counts[r.module_id] = counts.get(r.module_id, 0) + 1
        return {
            "total": len(routed),
            "by_module": counts,
        }
