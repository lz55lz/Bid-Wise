from app.services.material_match_policy import requires_enterprise_material


def test_only_proof_bearing_requirement_categories_enter_material_matching() -> None:
    assert requires_enterprise_material("QUALIFICATION") is True
    assert requires_enterprise_material("SCORING") is True
    assert requires_enterprise_material("BUSINESS") is False
    assert requires_enterprise_material("PROJECT") is False
    assert requires_enterprise_material(None) is False
