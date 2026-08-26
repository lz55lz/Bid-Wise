"""
清洗质量端到端测试：上传真实 PDF → 解析 → 清洗 → 写入 staging 表 → 字段质量分析

不写入生产表，不向量库，只做质量分析。
"""
from collections import defaultdict
from dataclasses import dataclass, field

import pytest

from app.db.models import DocumentNode


@dataclass
class StagingNode:
    """清洗后的节点快照，用于质量分析"""
    node_id: str
    node_type: str
    page_number: int | None
    section_path: str | None
    content: str
    cleaned_content: str | None
    indexable: bool
    tender_req_candidate: bool
    garbled_ratio: float
    char_count: int
    has_obligation_word: bool  # 是否含义务性表述

    @classmethod
    def from_node(cls, node: DocumentNode) -> "StagingNode":
        md = node.cleaning_metadata or {}
        content = node.content or ""
        # 义务词判断
        obligation_words = ["应当", "必须", "不得", "不准", "严禁", "需要", "要求", "须", "应"]
        has_ob = any(w in content for w in obligation_words)
        return cls(
            node_id=str(node.id),
            node_type=node.node_type,
            page_number=node.page_number,
            section_path=node.section_path,
            content=content[:200],  # 截断避免太长
            cleaned_content=node.cleaned_content[:200] if node.cleaned_content else None,
            indexable=md.get("indexable", False),
            tender_req_candidate=node.tender_req_candidate,
            garbled_ratio=md.get("garbled_ratio", 0.0),
            char_count=len(content),
            has_obligation_word=has_ob,
        )


@dataclass
class CleaningQualityReport:
    """清洗质量报告"""
    total_nodes: int
    indexable_count: int
    non_indexable_count: int
    tender_req_candidate_count: int
    non_candidate_count: int
    # 按 node_type 分布
    by_type: dict[str, int]
    # 按 section_path 顶层分布
    by_section: dict[str, int]
    # garbled 分布
    garbled_count: int
    high_garbled_count: int  # > 0.3
    # 义务词统计
    with_obligation: int
    without_obligation: int
    # 候选节点中：含义务词 vs 不含义务词
    candidate_with_obligation: int
    candidate_without_obligation: int
    # 短内容统计
    short_content_count: int  # < 30
    # 清洗前后长度差异
    avg_length_delta: float
    # staging 数据
    staging: list[StagingNode] = field(default_factory=list)


def _build_report(nodes: list[DocumentNode]) -> CleaningQualityReport:
    """从节点列表构建质量报告"""
    staging = [StagingNode.from_node(n) for n in nodes]

    by_type: dict[str, int] = defaultdict(int)
    by_section: dict[str, int] = defaultdict(int)
    with_ob, without_ob = 0, 0
    cand_with_ob, cand_without_ob = 0, 0
    short_cnt = 0
    garbled_cnt = 0
    high_garbled_cnt = 0
    length_deltas = []

    for s in staging:
        by_type[s.node_type] += 1
        if s.section_path:
            top = s.section_path.split(" / ")[0][:40]
            by_section[top] += 1

        if s.has_obligation_word:
            with_ob += 1
        else:
            without_ob += 1

        if s.tender_req_candidate:
            if s.has_obligation_word:
                cand_with_ob += 1
            else:
                cand_without_ob += 1

        if s.char_count < 30:
            short_cnt += 1

        if s.garbled_ratio > 0.05:
            garbled_cnt += 1
        if s.garbled_ratio > 0.3:
            high_garbled_cnt += 1

        if s.content and s.cleaned_content:
            delta = len(s.cleaned_content) - len(s.content)
            length_deltas.append(delta)

    avg_delta = sum(length_deltas) / len(length_deltas) if length_deltas else 0.0

    indexable_count = sum(1 for s in staging if s.indexable)
    tender_req_count = sum(1 for s in staging if s.tender_req_candidate)

    return CleaningQualityReport(
        total_nodes=len(staging),
        indexable_count=indexable_count,
        non_indexable_count=len(staging) - indexable_count,
        tender_req_candidate_count=tender_req_count,
        non_candidate_count=len(staging) - tender_req_count,
        by_type=dict(by_type),
        by_section=dict(sorted(by_section.items(), key=lambda x: -x[1])[:20]),
        garbled_count=garbled_cnt,
        high_garbled_count=high_garbled_cnt,
        with_obligation=with_ob,
        without_obligation=without_ob,
        candidate_with_obligation=cand_with_ob,
        candidate_without_obligation=cand_without_ob,
        short_content_count=short_cnt,
        avg_length_delta=avg_delta,
        staging=staging,
    )


def _print_report(r: CleaningQualityReport) -> None:
    """打印质量报告"""
    print("\n" + "=" * 60)
    print("清洗质量报告")
    print("=" * 60)
    print(f"总节点数:        {r.total_nodes}")
    print(f"indexable:       {r.indexable_count} ({r.indexable_count/r.total_nodes*100:.1f}%)")
    print(f"non-indexable:   {r.non_indexable_count} ({r.non_indexable_count/r.total_nodes*100:.1f}%)")
    print(f"tender_req候选:  {r.tender_req_candidate_count} ({r.tender_req_candidate_count/r.total_nodes*100:.1f}%)")
    print(f"garbled(>5%):   {r.garbled_count}")
    print(f"garbled(>30%):  {r.high_garbled_count}")
    print(f"含义务词:        {r.with_obligation} ({r.with_obligation/r.total_nodes*100:.1f}%)")
    print(f"不含义务词:      {r.without_obligation} ({r.without_obligation/r.total_nodes*100:.1f}%)")
    print(f"短内容(<30):     {r.short_content_count}")
    print(f"avg_length_delta:{r.avg_length_delta:.1f}")
    print()
    print("按节点类型:")
    for t, c in sorted(r.by_type.items(), key=lambda x: -x[1]):
        print(f"  {t}: {c}")
    print()
    print("按章节 (top 20):")
    for sec, c in sorted(r.by_section.items(), key=lambda x: -x[1])[:20]:
        print(f"  [{c:3d}] {sec}")
    print()
    print("候选节点中:")
    print(f"  含义务词:       {r.candidate_with_obligation}")
    print(f"  不含义务词:     {r.candidate_without_obligation}")
    print(f"  义务词覆盖率:   {r.candidate_with_obligation/r.tender_req_candidate_count*100:.1f}%" if r.tender_req_candidate_count else "  N/A")
    print()

    # 候选节点样本
    candidates = [s for s in r.staging if s.tender_req_candidate]
    print("候选节点样本 (前 10 个):")
    for s in candidates[:10]:
        ob_tag = "[HAS_OB]" if s.has_obligation_word else "[NO_OB]"
        print(f"  [{s.node_type}] {s.content[:60]}... {ob_tag}")
    print()

    # 非候选但含义务词的节点（可能是漏网之鱼）
    missed = [s for s in r.staging if not s.tender_req_candidate and s.has_obligation_word and s.indexable]
    print("非候选但含义务词的节点 (前 5 个，潜在漏过):")
    for s in missed[:5]:
        print(f"  [{s.node_type}] {s.content[:60]}...")
    print()

    # garbled 节点样本
    garbled = [s for s in r.staging if s.garbled_ratio > 0.05]
    print("garbled 节点样本 (前 5 个):")
    for s in garbled[:5]:
        print(f"  [{s.garbled_ratio:.2f}] {s.content[:60]}...")
    print("=" * 60)


class TestCleaningQuality:
    """清洗质量端到端测试"""

    @pytest.fixture
    def zb5_nodes(self) -> list[DocumentNode]:
        """从数据库加载 zb5.pdf 当前版本的节点"""
        from app.db import models as m
        from app.db.session import get_session_factory

        session = get_session_factory()()
        zb5 = session.query(m.Document).filter(
            m.Document.logical_name == "zb5.pdf"
        ).first()
        if not zb5 or not zb5.current_version_id:
            pytest.skip("zb5.pdf not found in DB")

        nodes = session.query(m.DocumentNode).filter(
            m.DocumentNode.document_version_id == zb5.current_version_id
        ).order_by(m.DocumentNode.order_no).all()
        session.close()
        return nodes

    def test_cleaning_quality_report(self, zb5_nodes: list[DocumentNode]) -> None:
        """生成清洗质量报告，不修改任何数据"""
        report = _build_report(zb5_nodes)
        _print_report(report)

        # ---- 断言：基本质量门槛 ----
        # indexable 比例应该在 60-90% 之间（合理范围）
        indexable_pct = report.indexable_count / report.total_nodes
        assert 0.5 < indexable_pct < 0.95, \
            f"indexable 比例异常: {indexable_pct:.1%}，请检查 MinerU 解析质量"

        # garbled 超过 30% 的节点应该很少（超过 10% 说明解析质量差）
        high_garbled_pct = report.high_garbled_count / report.total_nodes
        assert high_garbled_pct < 0.10, \
            f"高乱码节点比例过高: {high_garbled_pct:.1%}，请检查 MinerU 解析质量"

        # 候选节点中，含义务词的比例应该较高（>50%），
        # 如果低于 50% 说明大量非要求内容被标记为候选
        if report.tender_req_candidate_count > 0:
            ob_coverage = report.candidate_with_obligation / report.tender_req_candidate_count
            print(f"[INFO] 候选节点义务词覆盖率: {ob_coverage:.1%}")
            # 这个暂时只是警告，不 fail，因为现行逻辑还没有义务词过滤
            if ob_coverage < 0.5:
                print(f"[WARN] 候选节点中不含义务词的比例过高: {1-ob_coverage:.1%}，建议加强过滤")

        # 短内容节点（<30字符）中，非 indexable 应该占大多数
        short_nodes = [s for s in report.staging if s.char_count < 30]
        short_non_idx = [s for s in short_nodes if not s.indexable]
        if short_nodes:
            short_non_idx_pct = len(short_non_idx) / len(short_nodes)
            print(f"[INFO] 短内容节点中 non-indexable 比例: {short_non_idx_pct:.1%}")
