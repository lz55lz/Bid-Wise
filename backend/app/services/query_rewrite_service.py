"""Query Rewrite Service - Local query expansion for better retrieval.

Enterprise RAG pattern:
1. Query Understanding - classify intent
2. Query Expansion - expand with domain synonyms (local, no LLM)
3. Local Query Expansion - pattern-based fallback for recall deficiency
"""
import logging
import re
from dataclasses import dataclass
from enum import StrEnum

logger = logging.getLogger(__name__)


class QueryType(StrEnum):
    """Query intent classification."""
    GREETING = "greeting"          # "你好"、"谢谢"、"再见" → 跳过 RAG
    CHITCHAT = "chitchat"         # 闲聊 → 跳过 RAG
    DEFINITION = "definition"      # "什么是X", "X的定义"
    LIST = "list"                  # "有哪些", "包括哪些", "流程步骤"
    FACTUAL = "factual"            # "什么时候", "是多少"
    PROCEDURAL = "procedural"      # "怎么办", "如何处理"
    COMPARISON = "comparison"      # "X和Y区别"
    DEFAULT = "default"


# 政府采购领域同义词扩展表（口语→标准术语）
_QUERY_SYNONYMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    # 招标相关
    "招标": (("招标投标", "招投标", "招标采购", "采购招标"), ("招标投标",)),
    "投标": (("投标文件", "投标单位", "投标商"), ("投标",)),
    "中标": (("中标人", "中标结果", "中标公告", "中标候选人"), ("中标",)),
    "开标": (("开标时间", "开标地点", "开标程序", "开标现场"), ("开标时间", "开标地点")),
    "招标人": (("招标单位", "招标方", "招标机构"), ("招标人",)),
    "投标人": (("投标单位", "投标商", "投标方"), ("投标人",)),
    "采购人": (("采购单位", "采购机构", "采购方"), ("采购人",)),
    "供应商": (("投标人", "投标商", "供货商"), ("供应商",)),
    "招标文件": (("招标文书", "标书", "招标文档"), ("招标文件",)),
    "投标文件": (("标书", "投标文书", "投标文档"), ("投标文件",)),
    # 评标相关
    "评标": (("评审", "评标委员会", "评标方法"), ("评标",)),
    "废标": (("招标失败", "投标无效", "流标"), ("废标",)),
    "质疑": (("异议", "投诉", "质疑投诉"), ("质疑",)),
    "合同": (("采购合同", "中标合同", "合同签订"), ("合同",)),
    "预算": (("采购预算", "预算金额", "项目预算"), ("预算",)),
    "资质": (("资格", "资质要求", "投标资质"), ("资质",)),
    "保证金": (("投标保证金", "履约保证金", "质量保证金"), ("保证金",)),
    "评分": (("综合评分", "评分标准", "评审得分"), ("评分",)),
    "公开招标": (("竞争性招标", "公开采购"), ("公开招标",)),
    "邀请招标": (("选择性招标", "邀请采购"), ("邀请招标",)),
    "单一来源": (("单一来源采购", "直接采购"), ("单一来源",)),
    "竞争性谈判": (("竞争性磋商", "谈判采购"), ("竞争性谈判",)),
    "询价": (("询价采购", "比价采购"), ("询价",)),
    # 时间地点相关
    "时间": (("时间表", "时间点", "截止时间", "提交时间"), ("时间",)),
    "地点": (("位置", "场所", "地址", "在哪里"), ("地点",)),
    "期限": (("有效期", "截止期限", "投标期限"), ("期限",)),
    # 金额相关
    "金额": (("价格", "报价", "预算金额", "最高限价"), ("金额",)),
    "报价": (("投标报价", "报价金额", "投标价格"), ("报价",)),
}


@dataclass(frozen=True, slots=True)
class QueryRewriteResult:
    """Result of query rewrite operation."""
    original_query: str
    expanded_query: str  # 向量检索用，含扩展词
    rerank_query: str    # reranker 用，精简版
    query_type: QueryType
    multi_queries: list[str]  # 多查询扩展列表（供 BM25 多次检索）


def expand_query(query: str) -> tuple[str, str]:
    """Expand query with domain synonyms.

    Returns (expanded_query, rerank_query):
    - expanded_query: 原 query + 全量扩展词 → 用于 embedding
    - rerank_query: 原 query + 强语义扩展 → 用于 cross-encoder rerank
      （避免堆砌属性词干扰 reranker 打分）
    """
    q = (query or "").strip()
    if not q:
        return q, q

    extras: list[str] = []
    rerank_extras: list[str] = []

    for term, (all_expansions, rerank_expansions) in _QUERY_SYNONYMS.items():
        if term in q:
            for e in all_expansions:
                if e not in q and e not in extras:
                    extras.append(e)
            for e in rerank_expansions:
                if e not in q and e not in rerank_extras:
                    rerank_extras.append(e)

    if not extras and not rerank_extras:
        return q, q

    expanded = q + " " + " ".join(extras) if extras else q
    rerank = q + " " + " ".join(rerank_extras) if rerank_extras else q
    return expanded, rerank


# 本地 query 扩展规则（WeKnora 方案，无 LLM）
_QUERY_EXPANSION_PATTERNS: tuple[tuple[str, str], ...] = (
    # (触发词正则, 替换模板)  - 抽取引号短语
    (r'"([^"]+)"', r'\1'),
)


def _local_query_expansion(query: str) -> list[str]:
    """WeKnora 方案：本地非 LLM 的 query 扩展。

    策略：
    1. 抽取引号内的短语（精确匹配词）
    2. 按中英文标点分段（问句词去除）
    3. 去停用词

    返回扩展后的 query 列表（最多 5 个）。
    """
    variants: list[str] = []

    # 1. 抽取引号短语
    quoted = re.findall(r'"([^"]+)"', query)
    variants.extend([t.strip() for t in quoted if t.strip()])

    # 2. 按分隔符分段（、，,；;。.！!？?）
    segments = re.split(r'[、，,；;。.！!？?]+', query)
    for seg in segments:
        cleaned = seg.strip()
        if cleaned and len(cleaned) >= 2:
            variants.append(cleaned)

    # 3. 去除问句词后缀
    stopwords = (
        "请问", "问一下", "我想知道", "想知道",
        "什么时候", "什么时间", "何时", "多少",
        "有哪些", "包括哪些", "有没有", "怎么",
    )
    cleaned_query = query
    for sw in stopwords:
        if cleaned_query.startswith(sw):
            cleaned_query = cleaned_query[len(sw):].strip()
        cleaned_query = cleaned_query.replace(sw, "").strip()

    if cleaned_query and cleaned_query not in variants:
        variants.append(cleaned_query)

    # 去重、长度过滤、限制 5 个
    seen: set[str] = set()
    result: list[str] = []
    for v in variants:
        norm = v.strip().lower()
        if norm and norm not in seen and len(norm) >= 2:
            seen.add(norm)
            result.append(v.strip())
            if len(result) >= 5:
                break

    return result


def classify_query(query: str) -> QueryType:
    """Classify query intent for adaptive retrieval strategy."""
    q = (query or "").strip()

    # 闲聊 / 寒暄 → 跳过 RAG
    greeting_patterns = (
        "你好", "您好", "hi", "hello", "hey",
        "谢谢", "感谢", "thx", "thanks",
        "再见", "拜拜", "bye",
        "在吗", "在不在", "你好呀", "你好啊",
    )
    if any(p in q for p in greeting_patterns):
        return QueryType.GREETING

    # 闲聊式提问（不期望知识库答案）
    chitchat_patterns = (
        "今天天气", "你喜欢", "你是谁", "你会什么",
        "讲个笑话", "猜谜语", "脑筋急转弯",
        "介绍一下你自己", "说说你自己",
    )
    if any(p in q for p in chitchat_patterns):
        return QueryType.CHITCHAT

    ql = q.lower()
    if any(w in ql for w in ("什么是", "定义", "概念", "含义", "什么叫")):
        return QueryType.DEFINITION
    if any(w in q for w in ("哪些", "包括", "流程", "步骤", "列举", "清单", "列表", "都有什么")):
        return QueryType.LIST
    if any(w in q for w in ("怎么办", "如何处理", "怎么解决", "怎么处理", "怎么操作")):
        return QueryType.PROCEDURAL
    if any(w in q for w in ("区别", "差异", "不同", "比较", "对比")):
        return QueryType.COMPARISON
    if any(w in q for w in ("什么时候", "公布时间", "施行时间", "是多少", "金额", "数量", "天数")):
        return QueryType.FACTUAL

    return QueryType.DEFAULT


def rewrite_query(query: str) -> QueryRewriteResult:
    """Main entry point for query rewriting.

    结果会被 Redis 缓存（TTL=1h），相同 query 直接命中缓存。
    """
    from app.core.cache import _query_cache_key, cache_get, cache_set

    # Cache hit
    cache_key = _query_cache_key(query)
    cached = cache_get(cache_key)
    if cached is not None:
        logger.debug("query rewrite cache hit: %s", query[:30])
        return QueryRewriteResult(
            original_query=cached["original_query"],
            expanded_query=cached["expanded_query"],
            rerank_query=cached["rerank_query"],
            query_type=QueryType(cached["query_type"]),
            multi_queries=cached.get("multi_queries", []),
        )

    # Cache miss: compute
    query_type = classify_query(query)
    expanded_query, rerank_query = expand_query(query)
    multi_queries = _local_query_expansion(query)

    logger.info(
        "query rewritten: type=%s original=%r expanded=%r rerank=%r variants=%s",
        query_type.value, query, expanded_query, rerank_query, multi_queries
    )

    result = QueryRewriteResult(
        original_query=query,
        expanded_query=expanded_query,
        rerank_query=rerank_query,
        query_type=query_type,
        multi_queries=multi_queries,
    )

    # Write cache (fire-and-forget, failure is silent)
    cache_set(cache_key, {
        "original_query": result.original_query,
        "expanded_query": result.expanded_query,
        "rerank_query": result.rerank_query,
        "query_type": result.query_type.value,
        "multi_queries": result.multi_queries,
    })

    return result
