"""Controlled project-fact vocabulary shared by extraction and report queries.

``ProjectField`` is the persisted fact store.  Before this registry existed,
the rule path wrote lower-case implementation names while the LLM path wrote
upper-case report names.  A single business fact could consequently split into
two rows and downstream readers had to guess which one was authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProjectFieldSpec:
    code: str
    label: str
    report_section: str
    requires_evidence: bool = True


_SPECS = (
    ProjectFieldSpec("PROJECT_NAME", "项目名称", "PROJECT_PROFILE"),
    ProjectFieldSpec("PROJECT_CODE", "项目编号", "PROJECT_PROFILE"),
    ProjectFieldSpec("PURCHASER", "采购人/招标人", "PROJECT_PROFILE"),
    ProjectFieldSpec("AGENCY", "招标代理机构", "PROJECT_PROFILE"),
    ProjectFieldSpec("LOCATION", "建设/交付地点", "PROJECT_PROFILE"),
    ProjectFieldSpec("BUDGET", "项目预算", "PROJECT_PROFILE"),
    ProjectFieldSpec("MAX_PRICE", "最高限价", "PROJECT_PROFILE"),
    ProjectFieldSpec("BID_AMOUNT", "投标报价", "PROJECT_PROFILE"),
    ProjectFieldSpec("BID_BOND", "投标保证金", "BID_SCHEDULE"),
    ProjectFieldSpec("BID_DEADLINE", "投标截止时间", "BID_SCHEDULE"),
    ProjectFieldSpec("BID_OPENING_AT", "开标时间", "BID_SCHEDULE"),
    ProjectFieldSpec("PROCUREMENT_METHOD", "采购方式", "PROJECT_PROFILE"),
    ProjectFieldSpec("EVALUATION_METHOD", "评标方法", "SCORING_RULES"),
    ProjectFieldSpec("TENDERER", "招标人", "PROJECT_PROFILE"),
    ProjectFieldSpec("LEGAL_REPRESENTATIVE", "法定代表人", "PROJECT_PROFILE"),
    ProjectFieldSpec("CONTACT_PERSON", "联系人", "PROJECT_PROFILE"),
    ProjectFieldSpec("CONTACT_PHONE", "联系电话", "PROJECT_PROFILE"),
    ProjectFieldSpec("CONTACT_EMAIL", "联系邮箱", "PROJECT_PROFILE"),
)

PROJECT_FIELD_SPECS = {spec.code: spec for spec in _SPECS}

# Existing rows are read compatibly.  All new writes use the upper-case code.
_LEGACY_ALIASES = {
    "bid_amount": "BID_AMOUNT",
    "budget": "BUDGET",
    "max_price": "MAX_PRICE",
    "deposit": "BID_BOND",
    "bid_deadline": "BID_DEADLINE",
    "project_number": "PROJECT_CODE",
    "purchaser": "PURCHASER",
    "tenderer": "TENDERER",
    "legal_representative": "LEGAL_REPRESENTATIVE",
    "contact_person": "CONTACT_PERSON",
    "contact_phone": "CONTACT_PHONE",
    "contact_email": "CONTACT_EMAIL",
}


def canonical_project_field_code(field_code: str) -> str:
    """Return the persisted code for a known code while safely preserving unknowns."""
    normalized = field_code.strip() if isinstance(field_code, str) else ""
    if not normalized:
        return normalized
    return _LEGACY_ALIASES.get(normalized, normalized.upper())


def compatible_project_field_codes(field_code: str) -> frozenset[str]:
    """Return canonical and historic spellings that represent the same fact."""
    canonical = canonical_project_field_code(field_code)
    aliases = {canonical}
    aliases.update(
        legacy for legacy, target in _LEGACY_ALIASES.items() if target == canonical
    )
    return frozenset(aliases)


def get_project_field_spec(field_code: str) -> ProjectFieldSpec | None:
    return PROJECT_FIELD_SPECS.get(canonical_project_field_code(field_code))
