from app.services.project_field_registry import (
    canonical_project_field_code,
    compatible_project_field_codes,
    get_project_field_spec,
)


def test_rule_and_llm_codes_share_one_canonical_field() -> None:
    assert canonical_project_field_code("bid_deadline") == "BID_DEADLINE"
    assert canonical_project_field_code("BID_DEADLINE") == "BID_DEADLINE"
    assert compatible_project_field_codes("BID_DEADLINE") == {
        "BID_DEADLINE", "bid_deadline"
    }


def test_registered_report_field_keeps_its_query_contract() -> None:
    spec = get_project_field_spec("deposit")

    assert spec is not None
    assert spec.code == "BID_BOND"
    assert spec.report_section == "BID_SCHEDULE"
    assert spec.requires_evidence is True
