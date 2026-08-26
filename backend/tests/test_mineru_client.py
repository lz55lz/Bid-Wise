from io import BytesIO
from zipfile import ZipFile

from app.integrations.mineru import HttpMinerUClient


def test_hosted_mineru_result_zip_prefers_content_list() -> None:
    archive = BytesIO()
    with ZipFile(archive, "w") as result_zip:
        result_zip.writestr(
            "result_content_list.json",
            '[{"type":"title","text":"资格要求","page_idx":0},'
            '{"type":"text","text":"提供有效资质证明","page_idx":0}]',
        )
        result_zip.writestr("result.md", "# Ignored when content list exists")

    nodes = HttpMinerUClient._result_nodes_from_zip(archive.getvalue())

    assert [(node.node_type, node.content, node.page_number) for node in nodes] == [
        ("SECTION", "资格要求", 1),
        ("PARAGRAPH", "提供有效资质证明", 1),
    ]


def test_hosted_mineru_result_zip_falls_back_to_markdown() -> None:
    archive = BytesIO()
    with ZipFile(archive, "w") as result_zip:
        result_zip.writestr("result.md", "# 资格要求\n提供有效资质证明")

    nodes = HttpMinerUClient._result_nodes_from_zip(archive.getvalue())

    assert [(node.node_type, node.content) for node in nodes] == [
        ("SECTION", "资格要求"),
        ("PARAGRAPH", "提供有效资质证明"),
    ]


def test_mineru_v2_preserves_page_hierarchy_and_table_type() -> None:
    nodes = HttpMinerUClient._content_list_v2_nodes([
        [
            {"type": "title", "content": {"level": 1, "title_content": [{"content": "第六章"}]}},
            {"type": "title", "content": {"level": 2, "title_content": [{"content": "资格要求"}]}},
            {"type": "paragraph", "content": {"paragraph_content": [{"content": "应提供资质"}]}},
            {"type": "table", "content": {"table_body": "|资质|要求|"}},
        ],
    ])

    assert [(node.node_type, node.page_number, node.section_path) for node in nodes] == [
        ("SECTION", 1, "第六章"),
        ("SECTION", 1, "第六章 / 资格要求"),
        ("PARAGRAPH", 1, "第六章 / 资格要求"),
        ("TABLE", 1, "第六章 / 资格要求"),
    ]
