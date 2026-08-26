from app.services.bid_pipeline.clean_service import _label_node


def test_node_labels_keep_title_out_and_mandatory_qualification_in() -> None:
    title = _label_node({
        "chunk_type": "section", "chunk_text": "第三章 资格要求", "section_path": "第三章",
    })
    requirement = _label_node({
        "chunk_type": "paragraph", "section_path": "第三章 资格要求",
        "chunk_text": "投标人必须提供有效资质证书，近三年类似业绩不少于 2 项。",
    })

    assert title["requirement_candidate"] is False
    assert requirement["domains"] == ["QUALIFICATION"]
    assert requirement["mandatory_signal"] is True
    assert requirement["requirement_candidate"] is True
