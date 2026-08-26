from copy import deepcopy

from app.services.bid_pipeline.preprocess import _build_chunk_annotations


def test_chunk_annotation_adds_categories_without_mutating_candidate_budget() -> None:
    chunks = [
        {
            "chunk_index": 12,
            "section_path": "第三章 投标人资格要求",
            "chunk_text": "投标人必须具备相应资质。",
            "tender_req_candidate": True,
        },
        {
            "chunk_index": 13,
            "section_path": "第三章 投标人资格要求",
            "chunk_text": "本章其余说明文本。",
            "tender_req_candidate": False,
        },
    ]
    original = deepcopy(chunks)

    annotations, updates = _build_chunk_annotations(chunks)

    assert annotations["第三章 投标人资格要求"] == ["CAT03"]
    assert updates == {("CAT03",): [12, 13]}
    assert chunks == original
