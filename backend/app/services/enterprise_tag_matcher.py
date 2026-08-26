"""Deterministic reverse retrieval from trusted enterprise tags to tender requirements.

Enterprise tags are maintained facts for this demo deployment.  They narrow
the Requirement search space and prove the enterprise-side condition; tender
Evidence remains the proof of what the tender actually required.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from app.db.models import EnterpriseMaterial, Requirement


@dataclass(frozen=True, slots=True)
class EnterpriseTagEvaluation:
    status: str
    reason: str
    missing_conditions: list[dict[str, object]]
    matched_tags: list[str]
    recall_score: int


@dataclass(frozen=True, slots=True)
class _TagFact:
    key: str
    value: object
    display: str


class EnterpriseTagMatcher:
    """Use fixed enterprise material attributes as queries; never invoke an LLM."""

    _CATEGORY_TYPES: dict[str, set[str]] = {
        "QUALIFICATION": {
            "QUALIFICATION", "CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"
        },
        "SCORING": {"CERTIFICATE", "PROJECT_EXPERIENCE", "PERSONNEL"},
    }
    _DIMENSION_ALIASES: dict[str, tuple[str, ...]] = {
        # Rule extraction persists machine-readable dimension codes.  Map
        # them to the same enterprise-tag vocabulary as the Chinese labels.
        "registered_builder_qualification": ("建造师资格", "建造师", "注册建造师"),
        "safety_production_certificate": (
            "安全生产考核合格证书",
            "安全生产考核",
            "安全考核",
        ),
        "labor_social_security_relation": ("劳动关系", "社保", "聘用"),
        "project_management_experience_years": ("工作经验", "从业年限", "经验年限"),
        "project_management_domain": (
            "铁路营业线施工经验",
            "铁路营业线",
            "铁路施工经验",
        ),
        "railway_business_line_construction_experience": (
            "铁路营业线施工经验",
            "铁路营业线",
            "铁路施工经验",
        ),
        "concurrent_employment": ("在建项目任职", "在建项目", "在岗情况"),
        "financial_capability": ("财务能力", "财务", "审计", "报表"),
        "建造师资格": ("建造师资格", "建造师", "注册建造师"),
        "安全生产考核合格证书": ("安全生产考核合格证书", "安全生产考核", "安全考核"),
        "劳动关系": ("劳动关系", "社保", "聘用"),
        "工作经验": ("工作经验", "从业年限", "经验年限"),
        "铁路营业线施工经验": ("铁路营业线施工经验", "铁路营业线", "铁路施工经验"),
        "在建项目任职": ("在建项目任职", "在建项目", "在岗情况"),
        "类似业绩": ("类似业绩", "业绩", "项目经验"),
        "财务能力": ("财务能力", "财务", "审计", "报表"),
        "信用": ("信用", "失信", "黑名单"),
    }
    _TEXT_MARKERS = (
        "资质", "营业执照", "安全", "建造师", "人员", "社保", "业绩", "合同",
        "竣工", "验收", "财务", "审计", "报表", "信用", "失信", "黑名单",
        "证书", "认证", "体系",
    )
    _NEGATIVE_MARKERS = ("无", "未", "否", "不具备", "失效", "冻结", "过期")
    _NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")

    def recall(
        self, requirement: Requirement, materials: list[EnterpriseMaterial]
    ) -> list[EnterpriseMaterial]:
        """Return only tag-relevant materials, ordered by reverse-retrieval score."""
        allowed_types = self._CATEGORY_TYPES.get(requirement.category, set())
        ranked: list[tuple[int, EnterpriseMaterial]] = []
        for material in materials:
            if material.material_type not in allowed_types:
                continue
            score = self._recall_score(requirement, material)
            if score > 0:
                ranked.append((score, material))
        ranked.sort(key=lambda item: (-item[0], str(item[1].id)))
        return [material for _, material in ranked[:3]]

    def evaluate(
        self,
        requirement: Requirement,
        material: EnterpriseMaterial,
        bid_deadline: date | None,
    ) -> EnterpriseTagEvaluation:
        facts = self._facts_for(material)
        recall_score = self._recall_score(requirement, material)
        if material.attributes.get("conflict") is True:
            return EnterpriseTagEvaluation(
                "UNCERTAIN", "企业标签标记为存在矛盾信息", [], [], recall_score
            )
        if (
            bid_deadline is not None
            and material.valid_to is not None
            and material.valid_to < bid_deadline
        ):
            return EnterpriseTagEvaluation(
                "UNCERTAIN", "企业标签对应材料在投标截止日前已失效", [], [], recall_score
            )

        conditions = self._conditions(requirement)
        if not conditions:
            return EnterpriseTagEvaluation(
                "UNCERTAIN",
                "招标要求未抽取出可比较条件，已召回相关企业标签，需人工确认",
                [],
                [],
                recall_score,
            )

        matched: list[str] = []
        missing: list[dict[str, object]] = []
        for condition in conditions:
            condition_matches = self._condition_matches(condition, facts, requirement)
            if condition_matches:
                matched.extend(fact.display for fact in condition_matches)
            else:
                missing.append(condition)

        matched = list(dict.fromkeys(matched))
        if not missing:
            return EnterpriseTagEvaluation(
                "MATCHED",
                f"企业标签满足：{'；'.join(matched[:6])}",
                [],
                matched,
                recall_score,
            )
        if matched:
            return EnterpriseTagEvaluation(
                "UNCERTAIN",
                f"企业标签部分满足（命中：{'；'.join(matched[:4])}）；待确认："
                f"{'、'.join(self._condition_label(item) for item in missing)}",
                missing,
                matched,
                recall_score,
            )
        return EnterpriseTagEvaluation(
            "MISSING",
            "企业标签未满足：" + "、".join(
                self._condition_label(item) for item in missing
            ),
            missing,
            [],
            recall_score,
        )

    def _recall_score(self, requirement: Requirement, material: EnterpriseMaterial) -> int:
        facts = self._facts_for(material)
        score = 0
        for condition in self._conditions(requirement):
            if self._facts_for_dimension(str(condition.get("dimension", "")), facts):
                score += 3
        requirement_text = self._normalise(
            f"{requirement.title or ''} {requirement.description or ''}"
        )
        tag_text = self._normalise(" ".join(fact.display for fact in facts))
        score += sum(
            1 for marker in self._TEXT_MARKERS
            if marker in requirement_text and marker in tag_text
        )
        return score

    def _condition_matches(
        self,
        condition: dict[str, object],
        facts: list[_TagFact],
        requirement: Requirement,
    ) -> list[_TagFact]:
        dimension = str(condition.get("dimension", ""))
        operator = self._canonical_operator(condition.get("operator"))
        expected = condition.get("value")
        if dimension.lower() == "evidence" or operator == "REQUIRED":
            return self._evidence_condition_matches(requirement, facts)
        candidates = self._facts_for_dimension(dimension, facts)
        return [
            fact for fact in candidates
            if self._value_matches(operator, expected, fact.value)
        ]

    def _evidence_condition_matches(
        self, requirement: Requirement, facts: list[_TagFact]
    ) -> list[_TagFact]:
        requirement_text = self._normalise(
            f"{requirement.title or ''} {requirement.description or ''}"
        )
        markers = [marker for marker in self._TEXT_MARKERS if marker in requirement_text]
        if not markers:
            return []
        return [
            fact for fact in facts
            if any(marker in self._normalise(fact.display) for marker in markers)
        ]

    def _facts_for_dimension(
        self, dimension: str, facts: list[_TagFact]
    ) -> list[_TagFact]:
        aliases = self._DIMENSION_ALIASES.get(dimension, (dimension,))
        normalized_aliases = [self._normalise(alias) for alias in aliases]
        return [
            fact for fact in facts
            if any(
                alias
                and (
                    alias in self._normalise(fact.key)
                    or self._normalise(fact.key) in alias
                )
                for alias in normalized_aliases
            )
        ]

    def _value_matches(self, operator: str, expected: object, actual: object) -> bool:
        if operator in {"GTE", "GT", "LTE", "LT"}:
            expected_number = self._number(expected)
            actual_number = self._number(actual)
            if expected_number is None or actual_number is None:
                return False
            return {
                "GTE": actual_number >= expected_number,
                "GT": actual_number > expected_number,
                "LTE": actual_number <= expected_number,
                "LT": actual_number < expected_number,
            }[operator]

        actual_text = self._normalise(actual)
        expected_values = self._expected_values(expected)
        if not actual_text:
            return False
        if any(value in actual_text or actual_text in value for value in expected_values if value):
            return True
        expected_text = "".join(expected_values)
        expects_negative = any(marker in expected_text for marker in self._NEGATIVE_MARKERS)
        actual_negative = any(marker in actual_text for marker in self._NEGATIVE_MARKERS)
        if expects_negative:
            return actual_negative
        return not actual_negative and operator in {"EQUALS", "REQUIRED", "CONTAINS_ONE_OF"}

    def _facts_for(self, material: EnterpriseMaterial) -> list[_TagFact]:
        facts: list[_TagFact] = []
        if material.name:
            facts.append(_TagFact("材料名称", material.name, f"材料名称={material.name}"))
        if material.level:
            facts.append(_TagFact("等级", material.level, f"等级={material.level}"))
        if material.amount is not None:
            facts.append(_TagFact("金额", material.amount, f"金额={material.amount}"))
        for key, value in self._flatten(material.attributes):
            facts.append(_TagFact(key, value, f"{key}={self._display(value)}"))
        return facts

    def _flatten(self, value: object, prefix: str = "") -> list[tuple[str, object]]:
        if isinstance(value, dict):
            pairs: list[tuple[str, object]] = []
            for key, child in value.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                pairs.extend(self._flatten(child, path))
            return pairs
        if isinstance(value, list):
            return [(prefix, child) for child in value if child not in (None, "")]
        return [] if value in (None, "") else [(prefix, value)]

    @staticmethod
    def _conditions(requirement: Requirement) -> list[dict[str, object]]:
        raw = (requirement.conditions or {}).get("all")
        return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    @classmethod
    def _expected_values(cls, value: object) -> list[str]:
        if isinstance(value, list):
            return [cls._normalise(item) for item in value]
        return [
            cls._normalise(item)
            for item in re.split(r"[、/；;]|或", str(value or ""))
        ]

    @classmethod
    def _number(cls, value: object) -> float | None:
        match = cls._NUMBER_RE.search(str(value))
        return float(match.group()) if match else None

    @staticmethod
    def _display(value: object) -> str:
        return str(value).replace("\n", " ").strip()

    @staticmethod
    def _normalise(value: object) -> str:
        normalized = re.sub(r"[\s，,。；;：:（）()【】\[\]《》<>]+", "", str(value or ""))
        return normalized.replace("及以上", "")

    @staticmethod
    def _condition_label(condition: dict[str, object]) -> str:
        return str(condition.get("dimension") or condition.get("value") or "未命名条件")

    @staticmethod
    def _canonical_operator(value: object) -> str:
        """Accept extractor-style operators as well as the domain contract."""
        operator = str(value or "").upper()
        return {
            "EQ": "EQUALS",
            "EQUAL": "EQUALS",
            "IN": "CONTAINS_ONE_OF",
            "GE": "GTE",
            "LE": "LTE",
        }.get(operator, operator)
