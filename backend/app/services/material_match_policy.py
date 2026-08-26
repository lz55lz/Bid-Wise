"""Shared boundary for requirements that can be proven by enterprise materials."""


MATERIAL_MATCH_CATEGORIES = frozenset({"QUALIFICATION", "SCORING"})
_ENTERPRISE_PROOF_MARKERS = (
    "资质", "营业执照", "安全生产", "项目经理", "建造师", "社保", "劳动关系",
    "业绩", "财务", "审计", "信用", "信誉", "证书",
)
_BID_DOCUMENT_MARKERS = (
    "评标委员会", "投标作否决", "投标报价", "算术错误", "低于成本",
    "投标文件", "投标有效期", "分包单位",
)


def requires_enterprise_material(category: str | None) -> bool:
    """Whether an extracted Requirement enters material matching and missing-material checks."""
    return category in MATERIAL_MATCH_CATEGORIES


def is_enterprise_material_requirement(requirement: object) -> bool:
    """Return whether a concrete Requirement can be evidenced by enterprise tags.

    Category is only an extraction hint. A generic review or bid-document
    obligation can be misclassified as ``SCORING``/``QUALIFICATION`` but must
    never become an enterprise-material gap merely because no tag matches it.
    """
    if not requires_enterprise_material(getattr(requirement, "category", None)):
        return False
    conditions = getattr(requirement, "conditions", None)
    items = conditions.get("all", []) if isinstance(conditions, dict) else []
    if any(
        isinstance(item, dict) and str(item.get("dimension", "")).lower() != "evidence"
        for item in items
    ):
        return True

    text = " ".join(
        str(value or "")
        for value in (
            getattr(requirement, "title", None),
            getattr(requirement, "description", None),
        )
    )
    return (
        any(marker in text for marker in _ENTERPRISE_PROOF_MARKERS)
        and not any(marker in text for marker in _BID_DOCUMENT_MARKERS)
    )
