"""项目问答的数据源规划。

数据源路由是业务策略而不是模型自由决策：项目会话中的事实问题始终先查项目 Evidence，
法规和报告只在问题明确需要时补充。这样避免为了强制检索而绕一轮 tool-calling agent。
"""

import re

from app.modules.conversations.assistant_context import RetrievalPlan
from app.modules.retrieval.query_rewrite import QueryIntent, classify_query

_LEGAL_WORDS = ("法律", "法规", "法条", "合法", "合规", "条例", "办法", "规定", "违法", "法律依据")
_LEGAL_SUPPLEMENT_WORDS = ("中标", "合同", "资格", "保证金", "质疑", "投诉", "废标")
_REPORT_WORDS = ("报告", "分析报告", "报告结论", "报告里", "报告中")
_CASUAL_UTTERANCES = frozenset(
    {
        "你好",
        "您好",
        "hello",
        "hi",
        "谢谢",
        "感谢",
        "你是谁",
        "你能做什么",
        "你可以做什么",
        "有什么功能",
    }
)
_FOLLOWUP_WORDS = ("它", "这个", "那个", "上述", "前面", "刚才", "那", "呢", "还有")
_PROJECT_REFERENCE_WORDS = (
    "本项目",
    "该项目",
    "当前项目",
    "招标文件",
    "项目材料",
    "原文",
    "这个",
    "那个",
    "上述",
    "前面",
    "刚才",
)
_REPORT_COMPARE_WORDS = ("一致", "对照", "原文", "依据", "为什么", "是否正确", "准确")
# 报告是 Evidence 的快照：问到这些事实型主题时必须回到项目原文核对，不能只信报告结论。
_REPORT_FACT_TOPIC_WORDS = ("风险", "保证金", "资格", "废标", "评分", "业绩", "工期", "违约")
_GENERIC_LEGAL_WORDS = (
    "招投标法",
    "政府采购法",
    "法律规定",
    "法规规定",
    "法条规定",
    "一般规定",
    "通常规定",
    "法定",
    "法律上限",
)
_ENTERPRISE_FIT_WORDS = (
    "企业适配",
    "企业匹配",
    "绑定企业",
    "适配度",
    "匹配度",
    "我司",
    "本公司",
    "我们公司",
    "材料缺口",
    "能不能投",
)
_ENTERPRISE_FIT_FOLLOWUP_WORDS = (
    "缺口",
    "第一个",
    "这几个",
    "都补齐",
    "补齐后",
    "哪份材料",
    "现有材料",
    "对应材料",
    "是否一致",
    "一致吗",
    "为什么",
    "优先处理",
)


def plan_retrieval(question: str, *, has_enterprise_fit_history: bool = False) -> RetrievalPlan:
    """用稳定、可测试的规则生成本轮数据源计划。"""
    normalized = question.strip().lower()
    compact = re.sub(r"[\s，。！？!?、,.]+", "", normalized)
    if compact in _CASUAL_UTTERANCES:
        return RetrievalPlan(mode="CASUAL", project=False)

    intent = classify_query(question)
    legal = any(word in normalized for word in _LEGAL_WORDS) or (
        intent is QueryIntent.PROCEDURAL
        and any(word in normalized for word in _LEGAL_SUPPLEMENT_WORDS)
    )
    report = any(word in normalized for word in _REPORT_WORDS)
    # 长期记忆目前只保存用户主动维护的偏好，体量受限且不作为项目事实来源。
    # 对所有非寒暄问题都召回，才能真正影响日常回答的表达方式；此前只有用户显式
    # 提到“我的偏好”才召回，导致记忆管理看起来没有作用。
    memory = True
    project_reference = any(word in normalized for word in _PROJECT_REFERENCE_WORDS)
    report_compare = report and any(word in normalized for word in _REPORT_COMPARE_WORDS)
    enterprise_fit = any(word in normalized for word in _ENTERPRISE_FIT_WORDS) or (
        has_enterprise_fit_history
        and any(word in normalized for word in _ENTERPRISE_FIT_FOLLOWUP_WORDS)
    )

    # 企业适配度是当前绑定企业、已确认材料与匹配任务的实时业务结果，不能只靠招标原文
    # 检索。该问题没有匹配结果时也应如实说明“尚未执行匹配”，而不是误报证据不足。
    if enterprise_fit:
        return RetrievalPlan(
            mode="ENTERPRISE_FIT",
            project=False,
            enterprise_fit=True,
            memory=memory,
            require_enterprise_fit=True,
        )

    if report:
        # “报告里怎么说”只要求当前报告；一旦涉及需要回到原文核对的事实型主题，
        # 或明确要和原文/项目事实对照，则强制同时检索项目 Evidence。
        report_fact_topic = any(word in normalized for word in _REPORT_FACT_TOPIC_WORDS)
        project = report_compare or project_reference or report_fact_topic
        return RetrievalPlan(
            mode="HYBRID" if legal or project else "REPORT",
            project=project,
            legal=legal,
            report=True,
            memory=memory,
            require_project=project,
            require_legal=legal,
            require_report=True,
        )

    if legal:
        # 项目会话中的法规判断通常需要“项目事实 + 法规”双证据；纯法规问法则可只查知识库。
        generic_legal = any(word in normalized for word in _GENERIC_LEGAL_WORDS)
        project = project_reference or (
            not generic_legal and any(word in normalized for word in _LEGAL_SUPPLEMENT_WORDS)
        )
        return RetrievalPlan(
            mode="HYBRID" if project else "LEGAL",
            project=project,
            legal=True,
            memory=memory,
            require_project=project,
            require_legal=True,
        )

    return RetrievalPlan(
        mode="PROJECT",
        project=True,
        memory=memory,
        require_project=True,
    )


def plan_global_legal_retrieval(question: str, *, has_history: bool = False) -> RetrievalPlan:
    """全局会话只在问题明确指向项目材料时才要求选择项目。"""
    plan = plan_retrieval(question)
    # 企业适配度必须读取某一项目的绑定企业与匹配结果；不能因为全局会话已有历史
    # 而被降级成纯法规问题。
    if plan.enterprise_fit:
        return plan
    normalized = question.strip().lower()
    # 全局会话的后续问题默认延续法律上下文；只有明确提到项目或材料才中断并请求项目。
    if (plan.legal or has_history) and not any(
        word in normalized for word in _PROJECT_REFERENCE_WORDS
    ):
        return RetrievalPlan(
            mode="LEGAL",
            project=False,
            legal=True,
            memory=plan.memory,
            require_legal=True,
        )
    return plan


def build_retrieval_query(question: str, history: list[tuple[str, str]]) -> str:
    """为短追问补一条最近用户问题上下文，不让代词追问丢失检索主题。"""
    normalized = question.strip()
    if len(normalized) > 40 or not any(word in normalized for word in _FOLLOWUP_WORDS):
        return normalized
    previous_user = next(
        (
            content.strip()
            for role, content in reversed(history)
            if role == "USER" and content.strip()
        ),
        "",
    )
    if not previous_user:
        return normalized
    return f"{previous_user}\n追问：{normalized}"[:2_000]
