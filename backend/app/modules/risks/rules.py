# ruff: noqa: E501
"""不依赖 LLM 的内置投标风险规则执行器。

这里仅保留可审计的领域判断代码，不负责保存规则配置。规则名称、等级、启停和
版本定义统一由 ``risk_rule_versions`` 管理，避免出现“代码规则”和“模板规则”两套
事实来源。
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from app.modules.matching.models import MaterialMatchResult
from app.modules.materials.models import EnterpriseMaterial
from app.modules.requirements.models import TenderRequirement


@dataclass(frozen=True, slots=True)
class RiskCandidate:
    rule_code: str
    risk_type: str
    severity: str
    subject: str
    title: str
    description: str
    trigger_data: dict[str, object]


@dataclass(frozen=True, slots=True)
class BuiltinRiskRuleSpec:
    """内置规则的初始化定义。

    ``all/message_template/evidence_selector`` 与旧项目 RuleVersion DSL 保持一致；
    具体的材料和需求遍历由同名执行器完成，避免让任意 JSON 表达式直接执行。
    """

    code: str
    name: str
    risk_type: str
    severity: str
    definition: dict[str, object]


BUILTIN_RISK_RULES: tuple[BuiltinRiskRuleSpec, ...] = (
    BuiltinRiskRuleSpec(
        code="DEADLINE_EXPIRED",
        name="投标截止时间已过",
        risk_type="TIME",
        severity="CRITICAL",
        definition={
            "all": [
                {"source": "project", "field": "bid_deadline", "op": "LT_NOW"},
                {"source": "project", "field": "bid_deadline", "op": "EXISTS"},
            ],
            "message_template": "投标截止时间已过",
            "evidence_selector": {"source": "project", "field_code": "bid_deadline"},
        },
    ),
    BuiltinRiskRuleSpec(
        code="BID_DEADLINE_MISSING",
        name="投标截止日缺失",
        risk_type="DATA_QUALITY",
        severity="MEDIUM",
        definition={
            "all": [{"source": "project", "field": "bid_deadline", "op": "NOT_EXISTS"}],
            "message_template": "投标截止日缺失，无法核验证书有效期",
            "evidence_selector": {"source": "project", "field_code": "bid_deadline"},
        },
    ),
    BuiltinRiskRuleSpec(
        code="CERTIFICATE_EXPIRED",
        name="企业证书在投标截止日前失效",
        risk_type="QUALIFICATION",
        severity="HIGH",
        definition={
            "all": [
                {
                    "source": "material",
                    "field": "valid_to",
                    "op": "DATE_BEFORE",
                    "value": {"source": "project", "field": "bid_deadline"},
                }
            ],
            "message_template": "证书在投标截止日前失效",
            "evidence_selector": {"source": "material", "field_code": "valid_to"},
        },
    ),
    BuiltinRiskRuleSpec(
        code="QUANTITATIVE_REQUIREMENT_UNMET",
        name="定量资格条件未满足",
        risk_type="QUALIFICATION",
        severity="HIGH",
        definition={
            "all": [{"source": "requirement", "field": "conditions", "op": "EXISTS"}],
            "message_template": "定量资格条件未满足",
            "evidence_selector": {"source": "requirement", "field_code": "conditions"},
        },
    ),
    BuiltinRiskRuleSpec(
        code="MANDATORY_EVIDENCE_MISSING",
        name="强制需求缺少满足材料",
        risk_type="DOCUMENT",
        severity="HIGH",
        definition={
            "all": [{"source": "requirement", "field": "is_mandatory", "op": "EQ", "value": True}],
            "message_template": "强制 Requirement 缺少材料证据",
            "evidence_selector": {"source": "requirement", "field_code": "is_mandatory"},
        },
    ),
)


def bid_deadline_missing_risk(deadline: date | None) -> list[RiskCandidate]:
    """截止日未知时只提示核验缺口，不把未知误报成材料过期。"""
    if deadline is not None:
        return []
    return [
        RiskCandidate(
            "BID_DEADLINE_MISSING",
            "DATA_QUALITY",
            "MEDIUM",
            "project:bid_deadline",
            "投标截止日缺失，无法核验证书有效期",
            "项目未手工填写投标截止日，且没有可用的人工确认抽取值；证书有效期风险未参与计算。",
            {"field": "bid_deadline", "verification": "SKIPPED"},
        )
    ]


def bid_deadline_expired_risk(deadline: date | None, today: date) -> list[RiskCandidate]:
    """截止日有值且已过期时，给出不可继续推进的高优先级风险。"""
    if deadline is None or deadline >= today:
        return []
    return [
        RiskCandidate(
            "DEADLINE_EXPIRED",
            "TIME",
            "CRITICAL",
            "project:bid_deadline",
            "投标截止时间已过",
            f"投标截止日为 {deadline.isoformat()}，早于当前日期，项目不应继续推进。",
            {"field": "bid_deadline", "bid_deadline": deadline.isoformat()},
        )
    ]


def certificate_expiry_risks(
    materials: list[EnterpriseMaterial], deadline: date | None
) -> list[RiskCandidate]:
    if deadline is None:
        return []
    return [
        RiskCandidate(
            "CERTIFICATE_EXPIRED",
            "QUALIFICATION",
            "HIGH",
            f"material:{item.id}",
            f"企业资质或证书已过期：{item.name}",
            f"有效期至 {item.valid_to.isoformat()}，早于投标截止日。",
            {"material_id": str(item.id), "valid_to": item.valid_to.isoformat()},
        )
        for item in materials
        if item.material_type in {"QUALIFICATION", "CERTIFICATE"}
        and item.valid_to
        and item.valid_to < deadline
    ]


def mandatory_missing_risks(
    requirements: list[TenderRequirement], matches: list[MaterialMatchResult]
) -> list[RiskCandidate]:
    satisfied = {item.requirement_id for item in matches if item.final_status == "MATCHED"}
    return [
        RiskCandidate(
            "MANDATORY_EVIDENCE_MISSING",
            "DOCUMENT",
            "HIGH",
            f"requirement:{item.id}",
            f"强制需求缺少满足材料：{item.title}",
            "该需求已确认且为强制要求，但没有已满足的企业材料匹配结果。",
            {"requirement_id": str(item.id)},
        )
        for item in requirements
        if item.is_mandatory and item.id not in satisfied
    ]


def quantitative_requirement_risks(
    requirements: list[TenderRequirement], materials: list[EnterpriseMaterial]
) -> list[RiskCandidate]:
    """还原旧项目的定量资格校验：仅识别明确的 count / amount 下限。"""
    candidates = [
        item
        for item in materials
        if item.material_type in {"QUALIFICATION", "CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"}
    ]
    output: list[RiskCandidate] = []
    for requirement in requirements:
        if requirement.category != "QUALIFICATION":
            continue
        for dimension, expected in _quantitative_conditions(requirement.conditions):
            actual = _max_material_value(candidates, dimension)
            if actual is not None and actual >= expected:
                continue
            label = "数量" if dimension == "count" else "金额"
            actual_label = "无可用材料" if actual is None else str(actual)
            output.append(
                RiskCandidate(
                    "QUANTITATIVE_REQUIREMENT_UNMET",
                    "QUALIFICATION",
                    "HIGH",
                    f"requirement:{requirement.id}:{dimension}",
                    f"定量资格条件未满足：{requirement.title}",
                    f"“{requirement.title}”要求{label}不低于 {expected}，已确认材料最高为 {actual_label}。",
                    {
                        "requirement_id": str(requirement.id),
                        "dimension": dimension,
                        "required": str(expected),
                        "actual": None if actual is None else str(actual),
                    },
                )
            )
    return output


def _quantitative_conditions(value: object) -> list[tuple[str, Decimal]]:
    if not isinstance(value, dict) or not isinstance(value.get("all"), list):
        return []
    output: list[tuple[str, Decimal]] = []
    for item in value["all"]:
        if not isinstance(item, dict):
            continue
        dimension, operator = item.get("dimension"), item.get("operator")
        if dimension not in {"count", "amount"} or operator != "GTE":
            continue
        try:
            expected = Decimal(str(item.get("value")))
        except (InvalidOperation, TypeError, ValueError):
            continue
        if expected >= 0:
            output.append((dimension, expected))
    return output


def _max_material_value(materials: list[EnterpriseMaterial], dimension: str) -> Decimal | None:
    values: list[Decimal] = []
    for material in materials:
        raw_value = material.amount if dimension == "amount" else material.attributes.get("count")
        if raw_value is None:
            continue
        try:
            values.append(Decimal(str(raw_value)))
        except (InvalidOperation, TypeError, ValueError):
            continue
    return max(values) if values else None
