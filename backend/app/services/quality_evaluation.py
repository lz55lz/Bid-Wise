"""Deterministic, document-scoped quality metrics for extraction regression tests.

The evaluator deliberately does not ask an LLM to judge an LLM. A human-maintained
golden set describes business facts that must be found, their source page and their
expected classification. This makes it suitable for CI and for comparing pipeline
runs over time.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class GoldenSetError(ValueError):
    """Raised when a golden set cannot be evaluated unambiguously."""


@dataclass(frozen=True)
class RequirementCandidate:
    id: str
    category: str
    content: str
    mandatory: bool
    review_status: str
    evidence_pages: frozenset[int]
    source_evidence_count: int = 0


@dataclass(frozen=True)
class ProjectFieldCandidate:
    id: str
    field_code: str
    value_text: str
    review_status: str
    evidence_page: int | None
    source_evidence_count: int = 0


def _normalise(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).lower()


def _rate(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _term_groups(item: Mapping[str, Any]) -> list[list[str]]:
    raw_groups = item.get("term_groups")
    if raw_groups is not None:
        if not isinstance(raw_groups, list) or not raw_groups:
            raise GoldenSetError("term_groups 必须是非空二维数组")
        groups: list[list[str]] = []
        for group in raw_groups:
            if not isinstance(group, list) or not group or not all(
                isinstance(term, str) and term.strip() for term in group
            ):
                raise GoldenSetError("term_groups 的每组必须包含至少一个非空字符串")
            groups.append(group)
        return groups

    terms = item.get("terms")
    if not isinstance(terms, list) or not terms or not all(
        isinstance(term, str) and term.strip() for term in terms
    ):
        raise GoldenSetError("每条黄金项必须提供 terms 或 term_groups")
    mode = item.get("match_mode", "ANY")
    if mode == "ANY":
        return [terms]
    if mode == "ALL":
        return [[term] for term in terms]
    raise GoldenSetError("match_mode 只能是 ANY 或 ALL")


def _matches_terms(content: str, item: Mapping[str, Any]) -> bool:
    normalised = _normalise(content)
    return all(
        any(_normalise(term) in normalised for term in group)
        for group in _term_groups(item)
    )


def _expected_pages(item: Mapping[str, Any]) -> set[int]:
    if "page" in item:
        page = item["page"]
        if not isinstance(page, int) or page < 1:
            raise GoldenSetError("page 必须是正整数")
        return {page}
    pages = item.get("pages")
    if not isinstance(pages, list) or not pages or not all(
        isinstance(page, int) and page >= 1 for page in pages
    ):
        raise GoldenSetError("每条黄金项必须提供 page 或 pages")
    return set(pages)


def _status_counts(items: Sequence[RequirementCandidate | ProjectFieldCandidate]) -> dict[str, int]:
    return dict(sorted(Counter(item.review_status for item in items).items()))


def _threshold_failures(
    metrics: Mapping[str, Any], thresholds: Mapping[str, Any]
) -> dict[str, dict[str, float | None]]:
    failures: dict[str, dict[str, float | None]] = {}
    for metric_name, minimum in thresholds.items():
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            raise GoldenSetError(f"阈值 {metric_name} 必须是数值")
        actual = metrics.get(metric_name)
        if not isinstance(actual, (int, float)) or isinstance(actual, bool) or actual < minimum:
            failures[metric_name] = {
                "actual": actual if isinstance(actual, (int, float)) else None,
                "minimum": float(minimum),
            }
    return failures


def evaluate_extraction_quality(
    *,
    golden: Mapping[str, Any],
    requirements: Sequence[RequirementCandidate],
    project_fields: Sequence[ProjectFieldCandidate],
) -> dict[str, Any]:
    """Evaluate candidates using a document-scoped, human-authored golden set.

    Golden schema v2 supports ``term_groups``: every group must match at least one
    term. ``terms`` plus ``match_mode`` remains supported for v1 compatibility.
    """

    golden_requirements = golden.get("requirements", [])
    golden_fields = golden.get("project_fields", [])
    thresholds = golden.get("thresholds", {})
    if not isinstance(golden_requirements, list) or not isinstance(golden_fields, list):
        raise GoldenSetError("requirements 和 project_fields 必须是数组")
    if not isinstance(thresholds, Mapping):
        raise GoldenSetError("thresholds 必须是对象")

    matched_requirement_ids: set[str] = set()
    missed_requirements: list[str] = []
    mandatory_total = 0
    mandatory_matched = 0
    for item in golden_requirements:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise GoldenSetError("每条 requirement 黄金项必须具有字符串 id")
        if not isinstance(item.get("category"), str):
            raise GoldenSetError(f"黄金项 {item['id']} 缺少 category")
        expected_pages = _expected_pages(item)
        expected_mandatory = item.get("mandatory")
        if expected_mandatory is not None and not isinstance(expected_mandatory, bool):
            raise GoldenSetError(f"黄金项 {item['id']} 的 mandatory 必须是布尔值")
        matches = [
            candidate
            for candidate in requirements
            if candidate.category == item["category"]
            and bool(expected_pages & candidate.evidence_pages)
            and (expected_mandatory is None or candidate.mandatory is expected_mandatory)
            and _matches_terms(candidate.content, item)
        ]
        if matches:
            matched_requirement_ids.update(candidate.id for candidate in matches)
        else:
            missed_requirements.append(item["id"])
        if expected_mandatory is True:
            mandatory_total += 1
            mandatory_matched += bool(matches)

    matched_field_ids: set[str] = set()
    missed_fields: list[str] = []
    for item in golden_fields:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            raise GoldenSetError("每条 project_field 黄金项必须具有字符串 id")
        field_code = item.get("field_code")
        if not isinstance(field_code, str) or not field_code:
            raise GoldenSetError(f"字段黄金项 {item['id']} 缺少 field_code")
        expected_pages = _expected_pages(item)
        matches = [
            candidate
            for candidate in project_fields
            if candidate.field_code.lower() == field_code.lower()
            and candidate.evidence_page in expected_pages
            and _matches_terms(candidate.value_text, item)
        ]
        if matches:
            matched_field_ids.update(candidate.id for candidate in matches)
        else:
            missed_fields.append(item["id"])

    reviewed = [item for item in requirements if item.review_status in {"CONFIRMED", "REJECTED"}]
    confirmed = [item for item in reviewed if item.review_status == "CONFIRMED"]
    requirement_evidence_location_count = sum(bool(item.evidence_pages) for item in requirements)
    field_evidence_location_count = sum(item.evidence_page is not None for item in project_fields)
    metrics: dict[str, Any] = {
        "schema_version": golden.get("schema_version", 1),
        "requirement_golden_total": len(golden_requirements),
        "requirement_golden_matched": len(golden_requirements) - len(missed_requirements),
        "requirement_recall": _rate(
            len(golden_requirements) - len(missed_requirements), len(golden_requirements)
        ),
        "mandatory_requirement_golden_total": mandatory_total,
        "mandatory_requirement_matched": mandatory_matched,
        "mandatory_requirement_recall": _rate(mandatory_matched, mandatory_total),
        "requirement_candidate_count": len(requirements),
        "golden_candidate_coverage": _rate(len(matched_requirement_ids), len(requirements)),
        "requirement_evidence_location_rate": _rate(
            requirement_evidence_location_count, len(requirements)
        ),
        "requirement_review_status_counts": _status_counts(requirements),
        "review_sample_size": len(reviewed),
        "reviewed_precision": _rate(len(confirmed), len(reviewed)),
        "project_field_golden_total": len(golden_fields),
        "project_field_golden_matched": len(golden_fields) - len(missed_fields),
        "project_field_recall": _rate(
            len(golden_fields) - len(missed_fields), len(golden_fields)
        ),
        "project_field_candidate_count": len(project_fields),
        "project_field_evidence_location_rate": _rate(
            field_evidence_location_count, len(project_fields)
        ),
        "project_field_review_status_counts": _status_counts(project_fields),
        "missed_requirement_ids": missed_requirements,
        "missed_project_field_ids": missed_fields,
    }
    metrics["threshold_failures"] = _threshold_failures(metrics, thresholds)
    metrics["passed"] = not metrics["threshold_failures"]
    return metrics
