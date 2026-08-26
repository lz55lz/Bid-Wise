"""Run document-scoped extraction quality evaluation against a golden set.

Example:
  python scripts/evaluate_requirement_quality.py <project_id> \
    --document-version-id <version_id> --golden ../doc/evals/zb12-golden.json \
    --assert-thresholds --format markdown --output ../doc/evals/results/zb12.md

The command is read-only. It never changes project data or review states.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.db.models import DocumentVersion, Evidence, ProjectField, Requirement, RequirementEvidence
from app.db.session import get_session_factory
from app.services.quality_evaluation import (
    GoldenSetError,
    ProjectFieldCandidate,
    RequirementCandidate,
    evaluate_extraction_quality,
)


def _value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_value_text(item) for item in value.values())
    if isinstance(value, list):
        return " ".join(_value_text(item) for item in value)
    return "" if value is None else str(value)


def _render_markdown(result: dict[str, Any]) -> str:
    lines = ["# 文档抽取质量评测", "", f"- 通过阈值：`{result['passed']}`"]
    requirement_recall = (
        f"{result['requirement_golden_matched']}/"
        f"{result['requirement_golden_total']} ({result['requirement_recall']})"
    )
    mandatory_recall = (
        f"{result['mandatory_requirement_matched']}/"
        f"{result['mandatory_requirement_golden_total']} "
        f"({result['mandatory_requirement_recall']})"
    )
    field_recall = (
        f"{result['project_field_golden_matched']}/"
        f"{result['project_field_golden_total']} ({result['project_field_recall']})"
    )
    lines.extend(
        [
            "",
            "## Requirement",
            "",
            f"- 黄金集召回：{requirement_recall}",
            f"- 必选项召回：{mandatory_recall}",
            f"- 候选数：{result['requirement_candidate_count']}",
            f"- 证据页定位率：{result['requirement_evidence_location_rate']}",
            (
                f"- 人工复核样本：{result['review_sample_size']}，"
                f"已复核准确率：{result['reviewed_precision']}"
            ),
            f"- 待补项：{', '.join(result['missed_requirement_ids']) or '无'}",
            "",
            "## Project Fields",
            "",
            f"- 黄金集召回：{field_recall}",
            f"- 候选数：{result['project_field_candidate_count']}",
            f"- 证据页定位率：{result['project_field_evidence_location_rate']}",
            f"- 待补项：{', '.join(result['missed_project_field_ids']) or '无'}",
        ]
    )
    if result["threshold_failures"]:
        lines.extend(["", "## 未通过阈值", ""])
        lines.extend(
            f"- {name}: {value['actual']} < {value['minimum']}"
            for name, value in result["threshold_failures"].items()
        )
    return "\n".join(lines) + "\n"


def _load_candidates(
    *, project_id: UUID, document_version_id: UUID
) -> tuple[list[RequirementCandidate], list[ProjectFieldCandidate]]:
    session = get_session_factory()()
    try:
        requirements = list(
            session.scalars(
                select(Requirement).where(
                    Requirement.project_id == project_id, Requirement.deleted_at.is_(None)
                )
            )
        )
        evidence_by_requirement: dict[UUID, set[int]] = {}
        evidence_count_by_requirement: dict[UUID, int] = {}
        if requirements:
            rows = session.execute(
                select(RequirementEvidence.requirement_id, Evidence.page_number)
                .join(Evidence, Evidence.id == RequirementEvidence.evidence_id)
                .where(
                    RequirementEvidence.requirement_id.in_([item.id for item in requirements]),
                    Evidence.document_version_id == document_version_id,
                )
            )
            for requirement_id, page_number in rows:
                evidence_count_by_requirement[requirement_id] = (
                    evidence_count_by_requirement.get(requirement_id, 0) + 1
                )
                if page_number is not None:
                    evidence_by_requirement.setdefault(requirement_id, set()).add(page_number)
        scoped_requirements = [
            RequirementCandidate(
                id=str(item.id),
                category=item.category,
                content=f"{item.title} {item.description or ''}",
                mandatory=item.is_mandatory,
                review_status=item.review_status,
                evidence_pages=frozenset(evidence_by_requirement.get(item.id, set())),
                source_evidence_count=evidence_count_by_requirement.get(item.id, 0),
            )
            for item in requirements
            if evidence_count_by_requirement.get(item.id, 0) > 0
        ]

        fields = list(
            session.scalars(select(ProjectField).where(ProjectField.project_id == project_id))
        )
        primary_evidence_ids = [
            item.primary_evidence_id for item in fields if item.primary_evidence_id
        ]
        evidence_by_id = {}
        if primary_evidence_ids:
            evidence_by_id = {
                evidence.id: evidence
                for evidence in session.scalars(
                    select(Evidence).where(Evidence.id.in_(primary_evidence_ids))
                )
            }
        scoped_fields = []
        for field in fields:
            evidence = evidence_by_id.get(field.primary_evidence_id)
            if evidence is None or evidence.document_version_id != document_version_id:
                continue
            scoped_fields.append(
                ProjectFieldCandidate(
                    id=str(field.id),
                    field_code=field.field_code,
                    value_text=_value_text(field.value_json),
                    review_status=field.review_status,
                    evidence_page=evidence.page_number,
                    source_evidence_count=1,
                )
            )
        return scoped_requirements, scoped_fields
    finally:
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("project_id", type=UUID)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--document-version-id", type=UUID, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument(
        "--assert-thresholds", action="store_true", help="未达到 golden thresholds 时返回非零退出码"
    )
    args = parser.parse_args()
    golden = json.loads(args.golden.read_text(encoding="utf-8"))
    source = golden.get("source", {})

    session = get_session_factory()()
    try:
        version = session.get(DocumentVersion, args.document_version_id)
        if version is None:
            raise GoldenSetError("document_version_id 不存在")
        expected_sha256 = source.get("sha256") if isinstance(source, dict) else None
        if expected_sha256 and version.sha256 != expected_sha256:
            raise GoldenSetError("黄金集与指定 document_version 的 sha256 不一致")
    finally:
        session.close()

    requirements, fields = _load_candidates(
        project_id=args.project_id, document_version_id=args.document_version_id
    )
    result = evaluate_extraction_quality(
        golden=golden, requirements=requirements, project_fields=fields
    )
    rendered = (
        _render_markdown(result)
        if args.format == "markdown"
        else json.dumps(result, ensure_ascii=False, indent=2)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        text = rendered + ("" if rendered.endswith("\n") else "\n")
        args.output.write_text(text, encoding="utf-8")
    print(rendered)
    if args.assert_thresholds and not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
