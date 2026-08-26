"""L1 分类注释 + L2 精筛节点

L1（annotate_node）：按 MinerU 已解析节点的章节和正文匹配分类 code（CAT01-CAT11）
  输入：结构保真的 chunks
  输出：annotations: dict[section_path, list[category_code]]

L2（tagging_node）：候选 chunk + 分类 → 候选标签
  输入：chunks + annotations
  输出：candidate_tags: dict[chunk_id, list[tuple[tag_code, confidence]]]
"""
import re
from typing import Any

from sqlalchemy import text

from app.db.session import get_async_session_factory
from app.services.bid_pipeline.state import BidState
from app.services.observability import stage_task

# -------------------------------------------------------------------
# L1 粗筛：section_path → category_code 正则映射
# -------------------------------------------------------------------

# 每个分类的章节特征正则（按优先级排，优先匹配更具体的）
SECTION_PATTERNS: list[tuple[str, list[str]]] = [
    # CAT11 否决性条款 - 通常在最后或特定章节
    (
        "CAT11",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:否决|无效|废标|不予通过)",
            r".*(?:投标文件|响应文件).*(?:无效|否决|作废|不予受理)",
            r"^.*(?:废标|否决).*$",
        ],
    ),
    # CAT10 风险条款
    (
        "CAT10",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:风险|罚款|违约|限制|保密|知识产权)",
            r"^.*(?:罚款|违约金|赔偿责任|责任限制).*$",
            r"^.*(?:保密|知识产权|技术秘密).*$",
        ],
    ),
    # CAT09 合同条款
    (
        "CAT09",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:合同|分包|变更|索赔|争议|解除|终止|不可抗力)",
            r"^.*(?:合同条款|合同格式|合同范本).*$",
            r"^.*(?:分包|专业分包).*$",
        ],
    ),
    # CAT08 投标文件要求
    (
        "CAT08",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:投标文件|投标函|密封|签章|盖章|份数|电子标|加密)",
            r"^.*(?:投标文件格式|投标文件组成|装订|封面).*$",
            r"^.*(?:授权委托书|承诺函).*$",
        ],
    ),
    # CAT07 评标办法
    (
        "CAT07",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:评标|价格分|技术分|商务分|否决|基准价|评分)",
            r"^.*(?:评标办法|评审方法|综合评估|最低评标).*$",
            r"^.*(?:技术评分|商务评分|价格评分).*$",
        ],
    ),
    # CAT06 技术要求
    (
        "CAT06",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:技术|参数|规格|标准|验收|售后|培训|备件)",
            r"^.*(?:技术要求|技术规格|技术标准|性能指标).*$",
            r"^.*(?:施工方案|设计图纸|技术方案).*$",
        ],
    ),
    # CAT05 商务条款
    (
        "CAT05",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:保证金|付款|报价|工期|质保|结算|发票|违约金)",
            r"^.*(?:投标保证金|履约保证金|质量保证金).*$",
            r"^.*(?:付款方式|支付方式|预付款|进度款).*$",
            r"^.*(?:价格调整|竣工结算).*$",
        ],
    ),
    # CAT04 时间节点
    (
        "CAT04",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:时间|日期|截止|澄清|开标|评标|公示|合同)",
            r"^.*(?:报名时间|发售时间|投标截止|开标时间).*$",
            r"^.*(?:澄清|答疑).*$",
            r"^.*(?:中标公示|合同签订).*$",
        ],
    ),
    # CAT03 投标人资格要求
    (
        "CAT03",
        [
            r"第[一二三四五六七八九十\d]+条.*(?:资格|资质|业绩|人员|设备|保险|联合体|黑名单)",
            r"^.*(?:投标人资格|资质要求|资格审查).*$",
            r"^.*(?:项目经理|项目负责人|技术负责人).*$",
            r"^.*(?:营业执照|安全生产|资质等级).*$",
        ],
    ),
    # CAT02 招标人/代理机构
    (
        "CAT02",
        [
            r"^.*(?:招标人|采购人|代理机构|招标代理机构).*?(?:名称|地址|联系人|电话).*$",
            r"^.*(?:招标人|采购人).*?(?:名称|全称).*$",
            r"^.*(?:代理机构).*?(?:名称|地址|联系人).*$",
        ],
    ),
    # CAT01 项目基本信息
    (
        "CAT01",
        [
            r"^.*(?:项目名称|工程名称|采购项目).*$",
            r"^.*(?:项目编号|招标编号).*$",
            r"^.*(?:项目预算|最高限价|控制价).*$",
            r"^.*(?:资金来源|项目地点|建设地点).*$",
            r"^.*(?:招标方式|采购方式|公开招标|邀请招标).*$",
            r"^.*(?:工期|交货期|服务期).*$",
            r"^.*(?:项目规模|建设规模).*$",
        ],
    ),
]


def _build_section_paths(text: str) -> list[tuple[str, str]]:
    """把文本拆成 section_path + content 列表，保留顺序"""
    lines = text.split("\n")
    sections = []
    current_path = ""
    for line in lines:
        # 章节标题：第X条 或 # 标题 或 (X)
        m = re.match(r"^([第#(][^#\n(]+[）)]?\s*[：:：]?\s*)(.*)$", line.strip())
        if m:
            current_path = m.group(1).strip()
            content = m.group(2).strip()
            if current_path and content:
                sections.append((current_path, content))
        elif current_path and line.strip() and sections:
            # 续行，合并到上一个 section
            prev_path, prev_content = sections[-1]
            sections[-1] = (prev_path, prev_content + " " + line.strip())
    return sections


def _classify_section(section_path: str, content: str) -> list[str]:
    """对单个 section 进行分类，返回匹配的 category_code 列表"""
    matched: list[str] = []
    text = section_path + " " + content

    for cat_code, patterns in SECTION_PATTERNS:
        for pat in patterns:
            if re.search(pat, text, re.IGNORECASE):
                if cat_code not in matched:
                    matched.append(cat_code)
                break  # 一个 pattern 匹配即可，不继续尝试同一分类的其他 pattern

    return matched


def _build_chunk_annotations(
    chunks: list[dict[str, Any]],
) -> tuple[dict[str, list[str]], dict[tuple[str, ...], list[int]]]:
    """Classify the persisted parser chunks without changing the LLM budget.

    The clean stage owns ``tender_req_candidate``.  Annotation only adds
    category metadata, so a broad chapter heading cannot re-expand the small
    candidate queue selected from its child clauses.
    """
    annotations: dict[str, list[str]] = {}
    updates: dict[tuple[str, ...], list[int]] = {}
    for chunk in chunks:
        order_no = chunk.get("chunk_index")
        if order_no is None:
            continue
        section_path = str(chunk.get("section_path") or "")
        categories = _classify_section(section_path, str(chunk.get("chunk_text") or ""))
        if not categories:
            continue
        current = annotations.setdefault(section_path or "未归类章节", [])
        for category in categories:
            if category not in current:
                current.append(category)
        updates.setdefault(tuple(categories), []).append(int(order_no))
    return annotations, updates


@stage_task("annotate")
async def annotate_node(state: BidState) -> dict[str, Any]:
    """L1 粗筛节点：正则匹配 section → category_code，写入 bid_doc_chunk.category_codes"""
    import logging
    logger = logging.getLogger(__name__)

    chunks = list(state.get("chunks", []))
    annotations, updates = _build_chunk_annotations(chunks)
    if not chunks:
        # Historical state checkpoints can lack chunks. Keep their annotation
        # display behaviour, but deliberately do not mutate stored nodes.
        sections = _build_section_paths(state.get("raw_text", ""))
        annotations = {
            section_path: categories
            for section_path, content in sections
            if (categories := _classify_section(section_path, content))
        }
    logger.info(
        "[annotate] doc_id=%s chunks=%d classified_sections=%d",
        state["doc_id"], len(chunks), len(annotations),
    )

    if not annotations:
        annotations["全文概览"] = ["CAT01", "CAT02", "CAT04"]

    # 收集所有匹配到的 category_codes
    all_cats = list({cat for cats in annotations.values() for cat in cats})
    if not all_cats:
        all_cats = ["CAT01", "CAT02", "CAT04"]

    # 仅写入 document_nodes 分类元数据。候选标记只能由 clean 阶段设置，
    # 否则本阶段会把整章的普通文本重新送入 LLM。
    version_id = state.get("version_id")
    factory = get_async_session_factory()
    async with factory() as session:
        if version_id and updates:
            persisted = 0
            for categories, order_nos in updates.items():
                result = await session.execute(
                    text("""
                        UPDATE app.document_nodes
                        SET metadata = jsonb_set(
                            COALESCE(metadata, '{}'),
                            '{category_codes}',
                            to_jsonb(CAST(:cats AS text[]))
                        )
                        WHERE document_version_id = :version_id
                          AND order_no = ANY(:order_nos)
                    """),
                    {
                        "version_id": str(version_id),
                        "cats": list(categories),
                        "order_nos": order_nos,
                    },
                )
                persisted += result.rowcount
            await session.commit()
            logger.info(
                "[annotate] version_id=%s, classified_nodes=%d, cats=%s",
                version_id, persisted, all_cats,
            )
        # PR9: 删除兼容旧 bid_doc_chunk 的写入分支（孤儿表）
        else:
            logger.info(
                "[annotate] no persisted chunk annotations, skipping document_nodes update"
            )

    logger.info(
        "[annotate] doc_id=%s category_sections=%d category_codes=%s",
        state["doc_id"], len(annotations), all_cats,
    )
    return {
        "chunks": state.get("chunks", []),
        "annotations": annotations,
        "current_stage": "annotate",
        "stage_status": {"annotate": "done"},
    }
