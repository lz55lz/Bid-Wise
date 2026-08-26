from types import SimpleNamespace

from app.services.node_label_policy import NodeLabelPolicy


def _tag(tag_code: str, prompt: str) -> SimpleNamespace:
    return SimpleNamespace(
        tag_code=tag_code,
        tag_name="资质等级要求",
        category_code="CAT03",
        extraction_prompt=prompt,
    )


def test_tag_dictionary_refines_node_labels_and_records_policy_version() -> None:
    policy = NodeLabelPolicy.from_tag_rows([
        _tag("QUAL_QUALIFICATION", '查找"资质等级""施工资质"'),
    ])

    label = policy.label({
        "chunk_type": "paragraph",
        "section_path": "第三章 资格要求",
        "chunk_text": "投标人必须具备建筑工程施工总承包一级施工资质。",
    })

    assert label["domains"] == ["QUALIFICATION"]
    assert label["matched_tag_codes"] == ["QUAL_QUALIFICATION"]
    assert label["requirement_candidate"] is True
    assert label["policy_version"] == policy.version
    assert policy.source == "bid_tag_dict+baseline"


def test_policy_version_changes_when_the_effective_tag_terms_change() -> None:
    first = NodeLabelPolicy.from_tag_rows([
        _tag("QUAL_QUALIFICATION", '查找"资质等级"'),
    ])
    second = NodeLabelPolicy.from_tag_rows([
        _tag("QUAL_QUALIFICATION", '查找"施工资质"'),
    ])

    assert first.version != second.version


def test_content_domain_takes_priority_over_a_cross_domain_section_path() -> None:
    policy = NodeLabelPolicy()

    label = policy.label({
        "chunk_type": "paragraph",
        "section_path": "投标文件技术商务资格要求",
        "chunk_text": "投标人必须提供有效资质证书。",
    })

    assert label["domains"] == ["QUALIFICATION"]


def test_blocking_signal_is_persisted_separately_from_general_mandatory_signal() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "合同条款",
        "chunk_text": "投标人不得以任何方式转包本项目。",
    })

    assert label["mandatory_signal"] is True
    assert label["blocking_signal"] is True


def test_joint_venture_bidding_restriction_is_business_not_qualification() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "投标人须知",
        "chunk_text": "联合体各方不得再以自己名义单独或参加其他联合体在同一标段中投标。",
    })

    assert label["domains"] == ["BUSINESS"]
    assert label["analysis_scope"] == "BIDDER_REQUIREMENT"
    assert label["requirement_candidate"] is True


def test_financial_qualification_list_item_is_a_mandatory_bidder_requirement() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "投标人资格要求",
        "chunk_text": "（4）财务要求：提供近三年经第三方审计单位审计的财务报表。",
    })

    assert label["domains"] == ["QUALIFICATION"]
    assert label["analysis_scope"] == "BIDDER_REQUIREMENT"
    assert label["mandatory_signal"] is True
    assert label["requirement_candidate"] is True


def test_credit_blacklist_negative_eligibility_is_a_mandatory_bidder_requirement() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "投标人资格要求",
        "chunk_text": "投标人未在信用中国网站中被列入失信被执行人名单。",
    })

    assert label["domains"] == ["QUALIFICATION"]
    assert label["analysis_scope"] == "BIDDER_REQUIREMENT"
    assert label["mandatory_signal"] is True
    assert label["requirement_candidate"] is True


def test_non_bidder_contract_party_is_not_sent_to_requirement_extraction() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "合同条款",
        "chunk_text": "丙方应按约定完成施工并承担由此增加的费用。",
    })

    assert label["analysis_scope"] == "NON_BIDDER_PROCESS"
    assert label["requirement_candidate"] is False


def test_bidder_facing_scoring_criterion_is_kept_as_a_candidate() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "评标办法",
        "chunk_text": "投标人应按评分标准提交技术方案，最高得分为20分。",
    })

    assert label["analysis_scope"] == "BIDDER_REQUIREMENT"
    assert label["requirement_candidate"] is True


def test_bidder_mentioned_as_counterparty_does_not_make_owner_duty_matchable() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "投标保证金",
        "chunk_text": "招标人将在合同签订后向未中标的投标人退还投标保证金。",
    })

    assert label["analysis_scope"] == "NON_BIDDER_PROCESS"
    assert label["requirement_candidate"] is False


def test_reprocurement_outcome_is_not_mistaken_for_bidder_disqualification() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "招标投标程序",
        "chunk_text": "重新招标后投标人仍少于3个或者所有投标被否决的，应当依法重新招标。",
    })

    assert label["analysis_scope"] == "NON_BIDDER_PROCESS"
    assert label["requirement_candidate"] is False


def test_scoring_process_is_not_promoted_to_bidder_requirement() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "评标办法",
        "chunk_text": "评分分值计算保留小数点后两位，小数点后第三位四舍五入。",
    })

    assert label["analysis_scope"] == "SCORING_CRITERIA"


def test_tenderer_procedure_with_tenderer_instructions_is_not_an_obligation() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "中标候选人公示",
        "chunk_text": (
            "招标人应按照投标人须知前附表规定的媒介和期限公示中标候选人，"
            "公示期不少于3日。"
        ),
    })

    assert label["mandatory_signal"] is False
    assert label["analysis_scope"] == "NON_BIDDER_PROCESS"
    assert label["requirement_candidate"] is False


def test_bidder_complaint_procedure_is_not_an_enterprise_material_requirement() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "异议和投诉",
        "chunk_text": "投标人对评标结果提出投诉的，应当先向招标人提出异议。",
    })

    assert label["analysis_scope"] == "NON_BIDDER_PROCESS"
    assert label["requirement_candidate"] is False


def test_passive_reference_to_submitted_bid_is_not_an_implied_bidder_duty() -> None:
    label = NodeLabelPolicy().label({
        "chunk_type": "paragraph",
        "section_path": "开标程序",
        "chunk_text": "公布在投标截止时间前递交投标文件的投标人名称。",
    })

    assert label["analysis_scope"] == "UNSCOPED"
    assert label["requirement_candidate"] is False
