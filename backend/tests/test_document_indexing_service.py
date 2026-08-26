"""父子块切块测试（WeKnora SplitParentChild 移植）。

直接测试 app.integrations.chunking.split_parent_child 纯函数，不依赖 DB/API。
"""

from app.integrations.chunking import split_parent_child


def test_split_parent_child_long_text():
    content = "资格要求。" + ("A" * 1_300) + "。业绩要求。" + ("B" * 1_300)

    parents, children = split_parent_child(
        content, parent_size=1024, child_size=256
    )

    assert children, "expected children chunks"
    # 2611 字符：parent 1024 → 3 个父块；child 256 滑窗细分
    assert len(parents) >= 2
    assert all(len(c.content) <= 256 for c in children)
    # 子块 parent_index 指向有效父块
    linked = [c for c in children if c.parent_index >= 0]
    assert linked, "expected some children linked to parents"
    assert all(0 <= c.parent_index < len(parents) for c in linked)
    # 子块偏移已换算为整篇文本坐标且单调递增
    starts = [c.start for c in children]
    assert starts == sorted(starts)
    # 最后一个子块覆盖到文末
    assert children[-1].end == len(content)


def test_split_parent_child_short_text_no_parent():
    """短文本无需父块：子块内容即全部内容，parent_index=-1。"""
    content = "第三条 投标人应当具备独立法人资格。"

    parents, children = split_parent_child(
        content, parent_size=2048, child_size=384
    )

    assert parents == []
    assert len(children) == 1
    assert children[0].parent_index == -1
    assert children[0].content.strip() == content


def test_split_parent_child_breadcrumb_dedup():
    """父子面包屑合并时去掉重复的首行标题。"""
    content = (
        "# 第一章 招标公告\n\n"
        + ("项目概况内容。" * 100)
        + "\n\n## 1.1 招标条件\n\n"
        + ("招标条件内容。" * 100)
    )

    parents, children = split_parent_child(
        content, parent_size=512, child_size=128
    )

    assert children
    for child in children:
        lines = (child.context_header or "").split("\n")
        assert len(lines) == len(set(lines)), (
            f"duplicated breadcrumb lines: {child.context_header!r}"
        )
