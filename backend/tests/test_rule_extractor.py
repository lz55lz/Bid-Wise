from app.services.rule_extractor import extract_all


def test_rule_extraction_keeps_fact_types_separate() -> None:
    text = """
    项目预算：100万元
    最高限价：90万元
    投标报价：80万元
    投标截止时间：2026年8月25日15：00
    开标时间：2026年8月26日09：00
    联系电话：0871-66121636
    邮箱：bid@example.com
    采购人：甲方建设有限公司
    招标人：乙方招标有限公司
    """

    results = extract_all(text, node_id="node-1")
    values = {result.field_code: result.value for result in results}

    assert values["budget"] == "100"
    assert values["max_price"] == "90"
    assert values["bid_amount"] == "80"
    assert values["bid_deadline"] == "2026年8月25日15：00"
    assert values["contact_phone"] == "0871-66121636"
    assert values["contact_email"] == "bid@example.com"
    assert values["purchaser"] == "甲方建设有限公司"
    assert values["tenderer"] == "乙方招标有限公司"
    assert {result.node_id for result in results} == {"node-1"}


def test_rule_extraction_does_not_promote_generic_dates_or_phone_to_wrong_fields() -> None:
    results = extract_all("开标时间：2026年8月26日09：00\n联系电话：0871-66121636")
    fields = {result.field_code for result in results}

    assert "bid_deadline" not in fields
    assert "contact_phone" in fields
    assert "contact_email" not in fields
