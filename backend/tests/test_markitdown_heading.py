"""MarkItdownClient._is_likely_heading 启发式测试

参考 WeKnora patterns.go / profiler.go 的纯行级正则策略：
  - 行首锚定强信号（第X章/节/部分/篇、数字编号、一/二/三、）
  - 行首名词性标题词 + 长度 ≤ 30 + 不以句末标点结尾
  - 不含"条"避免误判"第一条 招标范围"为 heading

覆盖：
  - 真标题识别（中文章节、数字编号、行首名词）
  - 含关键词正文不被误判（"本项目采购货物..."）
  - 边界条件（超长/空行/英文/纯标点）
"""
import pytest

from app.integrations.markitdown_parser import MarkItdownClient

# -------------------------------------------------------------------
# 真标题应该被识别
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "第一章 总则",
        "第二章 投标人资格",
        "第三章 评标方法",
        "第十部分 附则",
        "1. 项目背景",
        "2. 投标人资格要求",
        "一、招标范围",
        "二、投标人须知",
        "（一）资质要求",
        "（二）业绩要求",
        "项目概况",
        "项目预算",
        "工程概况",
        "招标范围",
        "采购方式",
        "合同条款",
        "服务内容",
        # 带冒号的中文标题（修复前会被 endswith 误判）
        "项目概况：",
        "招标范围：",
        "投标人须知：",
    ],
)
def test_is_likely_heading_true(text: str):
    """行首锚定的真标题应被识别。"""
    assert MarkItdownClient._is_likely_heading(text) is True


# -------------------------------------------------------------------
# 正文/噪声应该不被识别
# -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # 含"项目/工程/采购"关键词但行首是动词/连接词的短正文
        "本项目工程位于北京市朝阳区",
        "本项目采购货物的技术参数详见附件",
        "投标保证金不得超过项目预算的百分之二",
        # "第一条"含"条"，WeKnora 规则不算 heading（避免被当章节标识）
        "第一条 招标范围",
        # 不在行首名词列表的纯名词
        "说明：",  # "说明"不在 noun_heading_prefixes
        # 边界条件
        "XXX",
        "",
        "。" * 30,  # 纯标点
    ],
)
def test_is_likely_heading_false(text: str):
    """含关键词正文/噪声/边界值不应被误判为 heading。"""
    assert MarkItdownClient._is_likely_heading(text) is False


# -------------------------------------------------------------------
# 长度边界
# -------------------------------------------------------------------


def test_is_likely_heading_rejects_long_lines():
    """超过 50 字符的行不应被识别为 heading。"""
    long_line = "项目概况：" + "详细内容" * 20  # 远超 50 字符
    assert MarkItdownClient._is_likely_heading(long_line) is False


def test_is_likely_heading_strips_whitespace():
    """行首尾空白应被去除后判断。"""
    assert MarkItdownClient._is_likely_heading("  项目概况  ") is True
    assert MarkItdownClient._is_likely_heading("  本项目工程位于北京市朝阳区  ") is False


# -------------------------------------------------------------------
# raw_text_to_chunks 集成验证（用中文，修复前会失败）
# -------------------------------------------------------------------


def test_raw_text_to_chunks_chinese_no_false_heading():
    """修复后：中文正文（含"项目"但行首是"本"）不应被误判为 heading。"""
    from app.services.document_ingest import raw_text_to_chunks

    text = (
        "# 项目概况章节\n"
        "本项目工程位于北京市朝阳区建设大道。\n"
        "本项目采购货物的技术参数详见附件。\n"
    )
    _, chunks = raw_text_to_chunks(text, doc_id="doc-zh")
    # 应只有 1 个 section（# 项目概况章节），不能把含"项目"的正文也当 section
    sections = [c for c in chunks if c["chunk_type"] == "section"]
    paragraphs = [c for c in chunks if c["chunk_type"] == "paragraph"]
    assert len(sections) == 1
    assert len(paragraphs) >= 1  # 修复前 paragraphs 被误吞为 sections


def test_raw_text_to_chunks_chinese_recognizes_noun_heading():
    """修复后：行首名词性标题（"项目概况："）应被识别为 heading。"""
    from app.services.document_ingest import raw_text_to_chunks

    text = (
        "项目概况：\n"
        "本项目工程位于北京市朝阳区建设大道。\n"
    )
    _, chunks = raw_text_to_chunks(text, doc_id="doc-zh2")
    # "项目概况："应被识别为 section
    sections = [c for c in chunks if c["chunk_type"] == "section"]
    assert len(sections) >= 1