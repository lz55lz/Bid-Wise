"""
CoverageChecker 反向校验测试

验证 CoverageChecker 是否能正确检测：
1. 关键词撒网扫描
2. 未覆盖的关键词命中
3. severity 分级（high/medium/low）
"""

import pytest

from app.services.coverage_checker import (
    CoverageChecker,
    CoverageResult,
    HitItem,
    Severity,
)


class TestCoverageCheckerScanHits:
    """关键词撒网扫描测试"""

    @pytest.fixture
    def mock_nodes(self):
        """模拟 DocumentNode 列表"""
        class MockNode:
            def __init__(self, order_no, content):
                self.order_no = order_no
                self.cleaned_content = content
        return [
            MockNode(1, "投标人应当具备有效的营业执照。"),
            MockNode(2, "投标保证金不得超过招标控制价的2%。"),
            MockNode(3, "本项目不接受联合体投标。"),
            MockNode(4, "投标人须在投标截止时间前递交投标文件。"),
            MockNode(5, "技术方案应包含施工组织设计。"),
        ]

    def test_scan_finds_obligations(self, mock_nodes):
        """扫描应该找到义务词"""
        checker = CoverageChecker.__new__(CoverageChecker)
        hits = checker.scan_hits(mock_nodes)

        # 应该找到"应当"、"不得"、"必须"等义务词
        words_found = {h.word for h in hits}
        print(f"Found words: {words_found}")

        assert len(hits) > 0, "应该有命中结果"

    def test_scan_finds_招标文件关键词(self, mock_nodes):
        """扫描应该找到招标文件相关关键词"""
        checker = CoverageChecker.__new__(CoverageChecker)
        hits = checker.scan_hits(mock_nodes)

        words_found = {h.word for h in hits}
        print(f"Found words: {words_found}")

        # 应该找到义务词（应当、必须、不得）
        obligation_words = {"应当", "必须", "不得"}
        assert words_found & obligation_words, f"应该找到义务词 {obligation_words} 中的词"

    def test_scan_dedup(self, mock_nodes):
        """同一行多次出现同一个词应该去重"""
        class MockNode:
            def __init__(self, order_no, content):
                self.order_no = order_no
                self.cleaned_content = content

        nodes = [MockNode(1, "投标人应当、应当、应当具备营业执照。")]
        checker = CoverageChecker.__new__(CoverageChecker)
        hits = checker.scan_hits(nodes)

        # "应当"出现3次但应该只算1次
        assert len(hits) == 1, "同一词的重复出现应该去重"


class TestCoverageCheckerSeverity:
    """Severity 分级测试"""

    @pytest.mark.parametrize("keyword,expected_severity", [
        ("废标", Severity.HIGH),
        ("无效投标", Severity.HIGH),
        ("否决", Severity.HIGH),
        ("拒收", Severity.HIGH),
        ("不予受理", Severity.HIGH),
        ("应当", Severity.MEDIUM),  # 不在 STRONG_KEYWORDS 中，所以是 MEDIUM
        ("必须", Severity.MEDIUM),  # 不在 STRONG_KEYWORDS 中，所以是 MEDIUM
        ("签字", Severity.LOW),  # 格式要求
        ("盖章", Severity.LOW),
    ])
    def test_severity_classification(self, keyword, expected_severity):
        """强判决词应该被标记为 HIGH"""
        checker = CoverageChecker.__new__(CoverageChecker)

        # 测试 _get_severity
        scope = checker._get_scope(keyword)
        severity = checker._get_severity(keyword, scope)

        assert severity == expected_severity, f"{keyword} 应该被标记为 {expected_severity}"


class TestCoverageCheckerCheck:
    """反向校验逻辑测试"""

    def test_check_with_full_coverage(self):
        """完全覆盖时应该没有 uncovered"""
        checker = CoverageChecker.__new__(CoverageChecker)

        hits = [
            HitItem(line=1, word="应当", text="投标人应当...", scope=["bid_phase"], severity=Severity.HIGH),
            HitItem(line=2, word="投标保证金", text="投标保证金不得超过...", scope=["bid_phase"], severity=Severity.MEDIUM),
        ]

        # 所有 hits 都被覆盖
        requirement_lines = {1: ["投标人资格要求"], 2: ["投标保证金要求"]}

        result = checker.check(hits, requirement_lines)

        assert result.uncovered == [], "完全覆盖时应该没有 uncovered"
        assert result.coverage_ratio == 1.0, "覆盖率应该是 100%"
        assert result.high_count == 0, "应该没有 high 级未覆盖"

    def test_check_with_partial_coverage(self):
        """部分覆盖时应该正确识别 uncovered"""
        checker = CoverageChecker.__new__(CoverageChecker)

        hits = [
            HitItem(line=1, word="应当", text="投标人应当...", scope=["bid_phase"], severity=Severity.HIGH),
            HitItem(line=2, word="投标保证金", text="投标保证金不得超过...", scope=["bid_phase"], severity=Severity.MEDIUM),
            HitItem(line=3, word="废标", text="弄虚作假者废标", scope=["bid_phase"], severity=Severity.HIGH),
        ]

        # 只有 line=1 被覆盖
        requirement_lines = {1: ["投标人资格要求"]}

        result = checker.check(hits, requirement_lines)

        assert len(result.uncovered) == 2, "应该有 2 个 uncovered"
        assert result.coverage_ratio == pytest.approx(1/3), "覆盖率应该是 33%"
        # high_count 是 uncovered 中 high severity 的数量
        assert result.high_count == 1, "应该有 1 个 high 级未覆盖（废标）"
        assert result.medium_count == 1, "应该有 1 个 medium 级未覆盖（投标保证金）"

    def test_check_with_no_coverage(self):
        """完全没有覆盖时应该全部 uncovered"""
        checker = CoverageChecker.__new__(CoverageChecker)

        hits = [
            HitItem(line=1, word="废标", text="弄虚作假者废标", scope=["bid_phase"], severity=Severity.HIGH),
            HitItem(line=2, word="无效投标", text="不符合要求按无效投标处理", scope=["bid_phase"], severity=Severity.HIGH),
        ]

        requirement_lines = {}  # 没有任何覆盖

        result = checker.check(hits, requirement_lines)

        assert len(result.uncovered) == 2, "应该全部 uncovered"
        assert result.coverage_ratio == 0.0, "覆盖率应该是 0%"
        assert result.high_count == 2, "应该有 2 个 high 级"
        assert len(result.warnings) > 0, "应该有告警"

    def test_is_healthy(self):
        """健康状态判断"""
        checker = CoverageChecker.__new__(CoverageChecker)

        # 覆盖率 90% 且无 high = 健康
        healthy = CoverageResult(
            total_hits=10, covered=9, uncovered=[],
            coverage_ratio=0.9, high_count=0, medium_count=1, low_count=0
        )
        assert healthy.is_healthy is True

        # 有 high 级未覆盖 = 不健康
        unhealthy = CoverageResult(
            total_hits=10, covered=9, uncovered=[],
            coverage_ratio=0.9, high_count=1, medium_count=0, low_count=0
        )
        assert unhealthy.is_healthy is False

        # 覆盖率低于 90% = 不健康
        unhealthy2 = CoverageResult(
            total_hits=10, covered=8, uncovered=[],
            coverage_ratio=0.8, high_count=0, medium_count=2, low_count=0
        )
        assert unhealthy2.is_healthy is False


class TestCoverageCheckerIntegration:
    """集成测试（需要真实数据库）"""

    @pytest.fixture
    def zb8_version_id(self):
        from uuid import UUID
        return UUID("2a8a45c0-5571-4629-83f2-cbf65c103ea2")

    @pytest.fixture
    def zb8_project_id(self):
        """从 zb8 文档找到 project_id"""
        from app.db import models as m
        from app.db.session import get_session_factory

        ZB8_VERSION_ID = UUID("2a8a45c0-5571-4629-83f2-cbf65c103ea2")
        session = get_session_factory()()
        version = session.query(m.DocumentVersion).get(ZB8_VERSION_ID)
        project_id = version.document.project_id if version and version.document else None
        session.close()
        return project_id

    @pytest.mark.skip(reason="需要真实数据库和已完成的提取结果")
    def test_check_from_db(self, zb8_version_id, zb8_project_id):
        """从数据库执行完整反向校验"""
        from app.db.session import get_session_factory

        session = get_session_factory()()
        checker = CoverageChecker(session)

        result = checker.check_from_db(zb8_version_id, zb8_project_id)

        print("\n=== Coverage Check Result ===")
        print(f"Total hits: {result.total_hits}")
        print(f"Covered: {result.covered}")
        print(f"Uncovered: {len(result.uncovered)}")
        print(f"Coverage ratio: {result.coverage_ratio:.1%}")
        print(f"High: {result.high_count}, Medium: {result.medium_count}, Low: {result.low_count}")
        print(f"Healthy: {result.is_healthy}")

        if result.uncovered:
            print("\n=== Uncovered Items (top 10) ===")
            for item in result.uncovered[:10]:
                print(f"  [{item.severity}] line={item.line} word={item.word}")
                print(f"    text: {item.text[:80]}")

        if result.warnings:
            print("\n=== Warnings ===")
            for w in result.warnings:
                print(f"  {w}")

        session.close()

        # 验证
        assert result.total_hits > 0, "应该有命中结果"
        # 注意：覆盖率可能不是 100%，这是正常的


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
