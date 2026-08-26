from app.schemas.rag import RagAnswerDraft


def test_rag_answer_draft_ignores_unrelated_model_fields():
    draft = RagAnswerDraft.model_validate(
        {"answer": "依据招标文件回答", "evidence_ids": [], "project_fields": [], "requirements": []}
    )

    assert draft.answer == "依据招标文件回答"
