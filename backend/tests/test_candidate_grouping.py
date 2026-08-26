"""
zb8 候选节点分组报告：验证去重逻辑是否有效

目标：在不调用 LLM 的情况下，分析 91 个候选节点的分布情况
- 按 node_id 去重：同一节点是否被多个模块重复处理
- 按 section_path 分组：同一 section 的节点是否被合并
- 识别潜在重复来源
"""
from collections import Counter
from uuid import UUID

import pytest

from app.db import models as m
from app.db.session import get_session_factory


@pytest.fixture
def zb8_candidates() -> list[m.DocumentNode]:
    """加载 zb8.pdf 的所有 tender_req_candidate=true 候选节点"""
    # zb8 document_version_id 固定（入库时已知）
    ZB8_VERSION_ID = UUID("2a8a45c0-5571-4629-83f2-cbf65c103ea2")
    session = get_session_factory()()
    nodes = session.query(m.DocumentNode).filter(
        m.DocumentNode.document_version_id == ZB8_VERSION_ID,
        m.DocumentNode.tender_req_candidate == True,  # noqa: E712
    ).order_by(m.DocumentNode.order_no).all()
    session.close()
    return nodes


def make_report(nodes: list[m.DocumentNode], path: str) -> None:
    """生成 Markdown 报告"""
    lines = [
        "# zb8 候选节点分组报告\n",
        f"**总候选节点: {len(nodes)}**\n",
    ]

    # 1. 按 node_type 分布
    type_dist = Counter(n.node_type for n in nodes)
    lines.append("## 按节点类型分布\n")
    for t, cnt in type_dist.most_common():
        lines.append(f"- {t}: {cnt}\n")

    # 2. 按 section_path 分组
    lines.append("\n## 按 section_path 分组\n")
    by_section: dict[str, list[m.DocumentNode]] = {}
    for n in nodes:
        key = (n.section_path or "")[:120] or "(无section_path)"
        by_section.setdefault(key, []).append(n)

    section_overview = []
    for sec, sec_nodes in sorted(by_section.items(), key=lambda x: -len(x[1])):
        section_overview.append((sec, len(sec_nodes)))
        lines.append(f"### {sec} ({len(sec_nodes)} 节点)\n")
        for n in sec_nodes:
            content_preview = (n.cleaned_content or n.content or "")[:120].replace("\n", " ")
            lines.append(f"- **[{n.node_type}]** order={n.order_no} | {content_preview}...\n")

    # 3. 重复内容检测：相同 cleaned_content 的节点
    lines.append("\n## 重复内容检测\n")
    content_seen: dict[str, list[m.DocumentNode]] = {}
    for n in nodes:
        key = (n.cleaned_content or n.content or "").strip()[:80]
        content_seen.setdefault(key, []).append(n)

    duplicates = {k: v for k, v in content_seen.items() if len(v) > 1}
    if duplicates:
        lines.append(f"**发现 {len(duplicates)} 组重复内容**\n")
        for k, v in duplicates.items():
            lines.append(f"### 重复: {k[:80]}... ({len(v)} 个节点)\n")
            for n in v:
                lines.append(f"- order={n.order_no}, section={str(n.section_path or '')[:60]}\n")
    else:
        lines.append("无重复内容。\n")

    # 4. 候选来源分析
    lines.append("\n## 候选来源分析\n")
    import re
    OBLIGATION_RE = re.compile("|".join([
        r"应当", r"必须", r"不得", r"不准", r"严禁", r"需要", r"要求", r"须",
    ]))
    CONTENT_PATTERNS = [
        r"投标人资格", r"投标保证金", r"履约保证金", r"资格审查",
        r"评标办法", r"评审办法", r"评审标准", r"实质性要求",
        r"分包", r"联合体", r"投标文件", r"投标有效期", r"投标报价",
        r"合同条款", r"技术标准", r"技术要求", r"规格", r"交货", r"验收",
        r"付款", r"结算",
    ]
    CONTENT_RE = re.compile("|".join(CONTENT_PATTERNS), re.IGNORECASE)
    CONTRACT_SEC_RE = re.compile(
        "|".join([r"合同条款", r"合同条件", r"付款", r"支付", r"结算",
                   r"履约", r"违约", r"质量保修", r"质保", r"保修",
                   r"竣工", r"验收标准", r"移交"]),
        re.IGNORECASE,
    )

    source_dist = Counter()
    for n in nodes:
        content = n.cleaned_content or ""
        sec = n.section_path or ""
        has_obligation = bool(OBLIGATION_RE.search(content))
        has_keyword = bool(CONTENT_RE.search(content))
        is_contract_bypass = bool(CONTRACT_SEC_RE.search(sec)) and len(content) >= 50

        if is_contract_bypass:
            source_dist["合同章节bypass"] += 1
        elif has_keyword and has_obligation:
            source_dist["关键词+义务词"] += 1
        elif has_keyword:
            source_dist["仅关键词"] += 1
        elif has_obligation:
            source_dist["仅义务词"] += 1
        else:
            source_dist["两者皆无"] += 1

    for src, cnt in source_dist.most_common():
        lines.append(f"- {src}: {cnt}\n")

    # 5. section 内节点数 TOP 20
    lines.append("\n## section 节点密度 TOP 20\n")
    lines.append("（每个 section 的候选节点越多 → 越容易产生重复提取）\n\n")
    for sec, cnt in section_overview[:20]:
        lines.append(f"- [{cnt:3d}] {sec}\n")

    md = "".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"报告已写入: {path}")


class TestCandidateGrouping:
    """zb8 候选节点分组分析（无 LLM 调用）"""

    def test_generate_grouping_report(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """生成候选节点分组报告到 doc/candidate_grouping.md"""
        import os

        from app.core.config import get_settings
        settings = get_settings()
        # 输出到项目 doc 目录
        doc_dir = os.path.join(os.path.dirname(__file__), "..", "doc")
        os.makedirs(doc_dir, exist_ok=True)
        out_path = os.path.join(doc_dir, "candidate_grouping.md")
        make_report(zb8_candidates, out_path)

    def test_duplicate_by_content(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """检测是否有节点内容完全重复（同一 node_id 或 content）"""
        ids = [n.id for n in zb8_candidates]
        assert len(ids) == len(set(ids)), f"发现重复 node_id！共 {len(ids)} 节点，{len(set(ids))} 唯一"

        content_seen: Counter = Counter((n.cleaned_content or n.content or "").strip()[:80] for n in zb8_candidates)
        dups = {k: v for k, v in content_seen.items() if v > 1}
        print(f"\n重复内容组数: {len(dups)}")
        for k, v in list(dups.items())[:5]:
            print(f"  [{v}次] {k[:60]}")

    def test_section_density(self, zb8_candidates: list[m.DocumentNode]) -> None:
        """统计每个 section 有多少候选节点"""
        by_sec: Counter = Counter((n.section_path or "")[:120] or "(无)" for n in zb8_candidates)
        top = by_sec.most_common(10)
        print("\n节点密度 TOP 10 sections:")
        for sec, cnt in top:
            print(f"  [{cnt:3d}] {sec[:70]}")

        # 检查是否有过多节点集中在同一 section（容易重复提取）
        overloaded = [(s, c) for s, c in by_sec.items() if c > 5]
        if overloaded:
            print(f"\n⚠️  {len(overloaded)} 个 section 超过 5 个候选节点:")
            for s, c in overloaded[:5]:
                print(f"  [{c}] {s[:70]}")
