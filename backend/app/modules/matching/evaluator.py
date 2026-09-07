"""材料与招标需求的确定性评估，不调用 LLM。"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import re

from app.modules.materials.models import EnterpriseMaterial
from app.modules.requirements.models import TenderRequirement


@dataclass(frozen=True, slots=True)
class MatchEvaluation:
    status: str
    reason: str
    missing_conditions: list[dict[str, object]]
    matched_facts: list[dict[str, object]]


class MaterialRequirementEvaluator:
    """以已确认材料事实和可比较需求条件为输入的可解释规则评估器。"""

    # 企业材料是演示用基础数据，因此用明确的材料类型和 attributes.tag_codes 建模，
    # 不使用名称相似度或 LLM 猜测资质是否等同。未声明即视为尚不能证明满足。
    _TAG_MATERIAL_TYPES: dict[str, set[str]] = {
        "QUAL_BUSINESS_LICENSE": {"QUALIFICATION", "CERTIFICATE"},
        "QUAL_REGISTERED_CAPITAL": {"FINANCE", "OTHER"},
        "QUAL_QUALIFICATION": {"QUALIFICATION", "CERTIFICATE"},
        "QUAL_SIMILAR_EXPERIENCE": {"PROJECT_EXPERIENCE"},
        "QUAL_FINANCIAL": {"FINANCE"},
        "QUAL_CREDIT": {"OTHER", "CERTIFICATE"},
        "QUAL_TAX": {"FINANCE", "CERTIFICATE"},
        "QUAL_SAFETY": {"CERTIFICATE", "QUALIFICATION"},
        "QUAL_PERSONNEL": {"PERSONNEL"},
        "QUAL_EQUIPMENT": {"OTHER"},
        "QUAL_INSURANCE": {"CERTIFICATE", "OTHER"},
    }

    def evaluate(
        self,
        requirement: TenderRequirement,
        material: EnterpriseMaterial | None,
        bid_deadline: date | None,
    ) -> MatchEvaluation:
        if material is None:
            return MatchEvaluation("MISSING", "没有可用企业材料", [{"dimension": "material"}], [])
        if material.status != "CONFIRMED":
            return MatchEvaluation(
                "MISSING", "材料尚未确认", [{"dimension": "material_status"}], []
            )
        if bid_deadline and material.valid_to and material.valid_to < bid_deadline:
            return MatchEvaluation(
                "MISSING", "材料在投标截止日前已失效", [{"dimension": "valid_to"}], []
            )
        facts = [{"key": "material_name", "value": material.name}]
        missing: list[dict[str, object]] = []
        uncertain: list[dict[str, object]] = []
        for condition in requirement.conditions.get("items", []):
            if not isinstance(condition, dict):
                continue
            dimension = str(condition.get("dimension", ""))
            expected = condition.get("value")
            if dimension == "amount":
                if not self._number_at_least(material.amount, expected):
                    missing.append(condition)
            elif dimension == "level":
                if material.level is None:
                    uncertain.append(condition)
                elif str(expected) not in {material.level, "", "None"}:
                    missing.append(condition)
            elif dimension == "QUAL_REGISTERED_CAPITAL":
                expected_amount = self._money_amount(expected)
                if expected_amount is None:
                    uncertain.append(condition)
                elif material.amount is None or material.amount < expected_amount:
                    missing.append(condition)
                else:
                    facts.append(
                        {
                            "key": "registered_capital",
                            "value": str(material.amount),
                            "required": str(expected_amount),
                        }
                    )
            elif dimension == "QUAL_SIMILAR_EXPERIENCE":
                experience_result = self._similar_experience_result(material, expected)
                if experience_result is False:
                    missing.append(condition)
                elif experience_result is None:
                    uncertain.append(condition)
                else:
                    facts.extend(experience_result)
            elif dimension == "QUAL_QUALIFICATION":
                qualification_result = self._qualification_result(material, expected)
                if qualification_result is False:
                    missing.append(condition)
                elif qualification_result is None:
                    uncertain.append(condition)
                else:
                    facts.extend(qualification_result)
            elif dimension == "QUAL_PERSONNEL":
                personnel_result = self._personnel_result(material, expected)
                if personnel_result is False:
                    missing.append(condition)
                elif personnel_result is None:
                    uncertain.append(condition)
                else:
                    facts.extend(personnel_result)
            elif dimension == "QUAL_FINANCIAL":
                financial_result = self._financial_result(material, expected)
                if financial_result is False:
                    missing.append(condition)
                elif financial_result is None:
                    uncertain.append(condition)
                else:
                    facts.extend(financial_result)
            elif dimension.startswith("QUAL_"):
                tag_result = self._tag_condition_result(material, dimension)
                if tag_result is False:
                    missing.append(condition)
                elif tag_result is None:
                    uncertain.append(condition)
            else:
                # 当前规则 DSL 无法解释的维度不代表企业不满足，只能进入语义/人工复核。
                uncertain.append(condition)
        if missing:
            return MatchEvaluation("MISSING", "材料明确未满足部分招标条件", missing, facts)
        if uncertain:
            return MatchEvaluation(
                "UNCERTAIN",
                "现有结构化事实不足以确定是否满足，需语义判断或人工确认",
                uncertain,
                facts,
            )
        return MatchEvaluation("MATCHED", "已确认材料满足可比较条件", [], facts)

    @classmethod
    def _tag_condition_result(
        cls, material: EnterpriseMaterial, tag_code: str
    ) -> bool | None:
        """True=明确满足，False=明确不满足，None=材料类型可能相关但事实不足。"""
        allowed_types = cls._TAG_MATERIAL_TYPES.get(tag_code)
        if allowed_types is None:
            return None
        if material.material_type not in allowed_types:
            return False
        tag_codes = material.attributes.get("tag_codes")
        if not isinstance(tag_codes, list):
            return None
        normalized = {str(item) for item in tag_codes}
        return tag_code in normalized


    @classmethod
    def _qualification_result(
        cls, material: EnterpriseMaterial, expected: object
    ) -> list[dict[str, object]] | bool | None:
        """资质名称必须有结构化事实才能判 MATCHED；只有 tag_code 不足以证明等级。"""
        if material.material_type not in {"QUALIFICATION", "CERTIFICATE"}:
            return False
        tag_result = cls._tag_condition_result(material, "QUAL_QUALIFICATION")
        if tag_result is False:
            return False
        required = expected if isinstance(expected, list) else [expected]
        required_names = [str(item).strip() for item in required if str(item).strip()]
        if not required_names:
            return None
        actual_raw = material.attributes.get("qualifications")
        if not isinstance(actual_raw, list):
            return None
        actual_names = {str(item).strip() for item in actual_raw if str(item).strip()}
        if not actual_names:
            return None
        missing = [item for item in required_names if item not in actual_names]
        if missing:
            # 资质名称存在行业简称/旧新名称映射，字符串不相等不能安全判定明确缺失。
            return None
        return [
            {
                "key": "qualifications",
                "value": sorted(actual_names),
                "required": required_names,
            }
        ]

    @classmethod
    def _personnel_result(
        cls, material: EnterpriseMaterial, expected: object
    ) -> list[dict[str, object]] | bool | None:
        """按角色、证书和最低经验年限核验结构化人员清单。"""
        if material.material_type != "PERSONNEL":
            return False
        tag_result = cls._tag_condition_result(material, "QUAL_PERSONNEL")
        if tag_result is False:
            return False
        requirements = expected if isinstance(expected, list) else [expected]
        requirements = [item for item in requirements if isinstance(item, dict)]
        persons = material.attributes.get("persons")
        if not requirements or not isinstance(persons, list):
            return None
        structured_people = [item for item in persons if isinstance(item, dict)]
        if not structured_people:
            return None
        facts: list[dict[str, object]] = []
        for requirement in requirements:
            role = str(requirement.get("role") or "").strip()
            cert = str(requirement.get("cert") or requirement.get("certificate") or "").strip()
            min_exp = cls._years_value(
                requirement.get("min_exp") or requirement.get("min_experience")
            )
            candidates = [
                person
                for person in structured_people
                if not role or str(person.get("role") or "").strip() == role
            ]
            if not candidates:
                return False if role else None
            matched = False
            uncertain = False
            for person in candidates:
                actual_cert = str(person.get("certificate") or "").strip()
                if cert:
                    if not actual_cert:
                        uncertain = True
                        continue
                    if cert != actual_cert:
                        # 证书名称可能存在专业/等级全称差异，留给语义/人工判断。
                        uncertain = True
                        continue
                if min_exp is not None:
                    actual_exp = cls._integer_value(person.get("experience_years"))
                    if actual_exp is None:
                        uncertain = True
                        continue
                    if actual_exp < min_exp:
                        continue
                matched = True
                facts.append(
                    {
                        "key": "personnel",
                        "role": person.get("role"),
                        "certificate": person.get("certificate"),
                        "experience_years": person.get("experience_years"),
                    }
                )
                break
            if not matched:
                if uncertain:
                    return None
                return False
        return facts

    @classmethod
    def _financial_result(
        cls, material: EnterpriseMaterial, expected: object
    ) -> list[dict[str, object]] | bool | None:
        """核验营业收入/资产负债率等可确定财务事实，缺字段时保守返回 UNCERTAIN。"""
        if material.material_type != "FINANCE":
            return False
        tag_result = cls._tag_condition_result(material, "QUAL_FINANCIAL")
        if tag_result is False:
            return False
        if not isinstance(expected, dict):
            return None
        facts: list[dict[str, object]] = []
        min_revenue = cls._money_amount(expected.get("min_revenue"))
        if min_revenue is not None:
            actual_revenue = cls._money_amount(material.attributes.get("annual_revenue"))
            if actual_revenue is None:
                return None
            if actual_revenue < min_revenue:
                return False
            facts.append(
                {
                    "key": "annual_revenue",
                    "value": str(actual_revenue),
                    "required": str(min_revenue),
                }
            )
        max_debt = cls._ratio_value(expected.get("max_debt_ratio"))
        if max_debt is not None:
            actual_debt = cls._ratio_value(material.attributes.get("debt_ratio"))
            if actual_debt is None:
                return None
            if actual_debt > max_debt:
                return False
            facts.append(
                {"key": "debt_ratio", "value": str(actual_debt), "required_max": str(max_debt)}
            )
        # report_years 只有材料显式提供覆盖年数时才能确定；缺失不能仅凭“财务材料”猜满足。
        required_years = cls._integer_value(expected.get("report_years"))
        if required_years is not None:
            actual_years = cls._integer_value(material.attributes.get("report_years"))
            if actual_years is None:
                return None
            if actual_years < required_years:
                return False
            facts.append({"key": "report_years", "value": actual_years, "required": required_years})
        return facts if facts else None

    @classmethod
    def _similar_experience_result(
        cls, material: EnterpriseMaterial, expected: object
    ) -> list[dict[str, object]] | bool | None:
        """比较可确定的业绩数量/金额；“同类型”等语义条件保守返回 UNCERTAIN。"""
        if material.material_type != "PROJECT_EXPERIENCE":
            return False
        requirements = expected if isinstance(expected, list) else [expected]
        facts: list[dict[str, object]] = []
        semantic_unknown = False
        for item in requirements:
            if not isinstance(item, dict):
                semantic_unknown = True
                continue
            min_count = cls._integer_value(item.get("min_count"))
            if min_count is not None:
                actual_count = cls._integer_value(material.attributes.get("project_count"))
                if actual_count is None:
                    semantic_unknown = True
                elif actual_count < min_count:
                    return False
                else:
                    facts.append(
                        {"key": "project_count", "value": actual_count, "required": min_count}
                    )
            min_amount = cls._money_amount(item.get("min_amount"))
            if min_amount is not None:
                if material.amount is None or material.amount < min_amount:
                    return False
                facts.append(
                    {
                        "key": "experience_amount",
                        "value": str(material.amount),
                        "required": str(min_amount),
                    }
                )
            expected_type = str(item.get("type") or "").strip()
            if expected_type:
                actual_type = str(material.attributes.get("experience_type") or "").strip()
                if not actual_type:
                    semantic_unknown = True
                elif actual_type != expected_type:
                    # 文本类型不做模糊猜测：不相等只能说明还需语义/人工判断，
                    # 不能据此直接判定 MISSING。
                    semantic_unknown = True
        return None if semantic_unknown else facts

    @staticmethod
    def _integer_value(value: object) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            return int(str(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _years_value(value: object) -> int | None:
        if value is None or isinstance(value, bool):
            return None
        match = re.search(r"(\d+)", str(value))
        return int(match.group(1)) if match else None

    @staticmethod
    def _ratio_value(value: object) -> Decimal | None:
        if value is None or isinstance(value, bool):
            return None
        text = str(value).strip().replace("％", "%")
        try:
            if text.endswith("%"):
                return Decimal(text[:-1].strip()) / Decimal("100")
            number = Decimal(text)
        except (InvalidOperation, ValueError):
            return None
        # 数据库示例使用 0.42；若结构化来源直接给 70，按百分数 70% 解释。
        return number / Decimal("100") if number > 1 else number

    @staticmethod
    def _money_amount(value: object) -> Decimal | None:
        """把人工确认/LLM 提取的常见人民币金额归一到“元”，无法确定则返回 None。"""
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, (int, float, Decimal)):
            try:
                return Decimal(str(value))
            except InvalidOperation:
                return None
        if isinstance(value, dict):
            for key in ("min_amount", "amount", "value"):
                if key in value:
                    parsed = MaterialRequirementEvaluator._money_amount(value.get(key))
                    if parsed is not None:
                        return parsed
            return None
        text = str(value).replace(",", "").replace("，", "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?", text)
        if match is None:
            return None
        try:
            amount = Decimal(match.group(1))
        except InvalidOperation:
            return None
        unit = match.group(2) or "元"
        if unit in {"亿元", "亿"}:
            amount *= Decimal("100000000")
        elif unit in {"万元", "万"}:
            amount *= Decimal("10000")
        return amount

    @staticmethod
    def _number_at_least(actual: Decimal | None, expected: object) -> bool:
        try:
            return actual is not None and actual >= Decimal(str(expected))
        except (InvalidOperation, ValueError):
            return False
