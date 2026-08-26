import asyncio

from app.services.bid_pipeline.clean_service import score_and_clean_chunks
from app.services.document_text_quality import assess_text_quality, indexability_gate


def test_chinese_tender_text_is_not_mistaken_for_garbled() -> None:
    quality = assess_text_quality("投标人必须提供有效资质证书，近三年类似业绩不少于2项。")

    assert quality.is_garbled is False
    assert quality.garbled_ratio == 0


def test_unexpected_script_from_pdf_mojibake_is_rejected() -> None:
    quality = assess_text_quality("αβγδεζηθικλμνξοπρστυφχψω")

    assert quality.unexpected_script_characters == quality.visible_characters
    assert quality.is_garbled is True


def test_contents_page_and_oversized_block_are_not_indexable() -> None:
    assert (
        indexability_gate("目录 第一章........................................1")
        == "CONTENTS_PAGE"
    )
    assert indexability_gate("投标人必须提供资质证书。" * 150) == "OVERSIZED_CHUNK"


def test_worker_cleaning_blocks_garbled_node_before_candidate_routing() -> None:
    chunks = [
        {
            "chunk_index": 1,
            "chunk_type": "paragraph",
            "section_path": "第三章 投标人资格要求",
            "chunk_text": "αβγδεζηθικλμνξοπρστυφχψω投标人必须提供资质证书。",
        }
    ]

    result = asyncio.run(score_and_clean_chunks(chunks, doc_id=1))
    node = result["chunks"][0]

    assert node["indexable"] is False
    assert node["tender_req_candidate"] is False
    assert node["node_labels"]["quality_gate"] == "GARBLED_TEXT"


def test_worker_cleaning_blocks_contents_and_oversized_nodes() -> None:
    chunks = [
        {
            "chunk_index": 1,
            "chunk_type": "paragraph",
            "section_path": "",
            "chunk_text": "目录 第一章........................................1",
        },
        {
            "chunk_index": 2,
            "chunk_type": "paragraph",
            "section_path": "第三章 投标人资格要求",
            "chunk_text": "投标人必须提供资质证书。" * 150,
        },
    ]

    result = asyncio.run(score_and_clean_chunks(chunks, doc_id=1))

    assert [node["quality_gate"] for node in result["chunks"]] == [
        "CONTENTS_PAGE", "OVERSIZED_CHUNK"
    ]
    assert all(node["indexable"] is False for node in result["chunks"])
    assert all(node["tender_req_candidate"] is False for node in result["chunks"])


def test_worker_candidate_budget_is_primary_section_based() -> None:
    chunks = [
        {
            "chunk_index": index,
            "chunk_type": "paragraph",
            "section_path": f"第三章 资格要求 / {index}.1 条款",
            "chunk_text": f"投标人必须提供有效资质证书，第 {index} 项。",
        }
        for index in range(8)
    ]

    result = asyncio.run(score_and_clean_chunks(chunks, doc_id=1))
    selected = [node for node in result["chunks"] if node["tender_req_candidate"]]
    deferred = [node for node in result["chunks"] if not node["tender_req_candidate"]]

    assert len(selected) == 4
    assert all(
        node["node_labels"]["selection_reason"] == "PRIMARY_SECTION_BUDGET"
        for node in selected
    )
    assert all(
        node["node_labels"]["selection_reason"] == "DEFERRED_BY_SECTION_BUDGET"
        for node in deferred
    )
