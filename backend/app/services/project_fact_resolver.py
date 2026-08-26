"""Resolve reviewed project facts into typed values for downstream analysis.

``ProjectField`` is the traceable extraction store.  ``TenderProject`` keeps
operator-maintained fields.  Consumers must not choose between them ad hoc:
this module makes the precedence, evidence and date precision explicit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories.project_field_repository import ProjectFieldRepository
from app.services.project_field_registry import compatible_project_field_codes

_DATE_WITH_OPTIONAL_TIME_RE = re.compile(
    r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"
    r"(?:\s*(?P<period>上午|下午)?\s*(?P<hour>\d{1,2})\s*(?:时|:|：)"
    r"\s*(?P<minute>\d{1,2})(?:\s*分)?)?"
)
_ISO_DATE_RE = re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$")


@dataclass(frozen=True, slots=True)
class ResolvedBidDeadline:
    """A bid deadline together with the precision that the source supports."""

    value: datetime | None
    precision: Literal["DATETIME", "DATE", "UNKNOWN"]
    source: Literal["PROJECT", "PROJECT_FIELD", "NONE"]
    field_id: UUID | None = None
    evidence_id: UUID | None = None

    @property
    def is_confirmed(self) -> bool:
        return self.value is not None

    @property
    def date(self) -> date | None:
        return self.value.date() if self.value is not None else None

    @property
    def evidence_ids(self) -> list[UUID]:
        return [] if self.evidence_id is None else [self.evidence_id]

    def is_expired(self, now: datetime) -> bool:
        """Date-only values expire after their calendar day, never at midnight."""
        if self.value is None:
            return False
        normalized_now = _as_utc(now)
        if self.precision == "DATE":
            return self.value.date() < normalized_now.date()
        return self.value < normalized_now

    def display_value(self) -> str | None:
        if self.value is None:
            return None
        if self.precision == "DATE":
            return self.value.date().isoformat()
        return self.value.isoformat()

    def fingerprint(self) -> dict[str, str | None]:
        """JSON-safe state used by task idempotency and analysis snapshots."""
        return {
            "value": self.value.isoformat() if self.value else None,
            "precision": self.precision,
            "source": self.source,
            "field_id": str(self.field_id) if self.field_id else None,
            "evidence_id": str(self.evidence_id) if self.evidence_id else None,
        }


class ProjectFactResolver:
    """Read only reviewed extracted facts; a manual project value takes priority."""

    def __init__(self, session: Session) -> None:
        self._fields = ProjectFieldRepository(session)

    def resolve_bid_deadline(self, project: Any) -> ResolvedBidDeadline:
        manual_deadline = getattr(project, "bid_deadline", None)
        if isinstance(manual_deadline, datetime):
            return ResolvedBidDeadline(
                value=_as_utc(manual_deadline),
                precision="DATETIME",
                source="PROJECT",
            )

        candidates = [
            field
            for field in self._fields.list_for_project(project.id)
            if field.field_code in compatible_project_field_codes("BID_DEADLINE")
            and field.review_status == "CONFIRMED"
        ]
        candidates.sort(
            key=lambda field: (
                field.confidence is not None,
                field.confidence or 0,
                field.updated_at,
                str(field.id),
            ),
            reverse=True,
        )
        for field in candidates:
            parsed = parse_bid_deadline((field.value_json or {}).get("value"))
            if parsed is not None:
                value, precision = parsed
                return ResolvedBidDeadline(
                    value=value,
                    precision=precision,
                    source="PROJECT_FIELD",
                    field_id=field.id,
                    evidence_id=field.primary_evidence_id,
                )
        return ResolvedBidDeadline(None, "UNKNOWN", "NONE")


def parse_bid_deadline(value: Any) -> tuple[datetime, Literal["DATETIME", "DATE"]] | None:
    """Parse rule/LLM field output without inventing a time component."""
    if isinstance(value, datetime):
        return _as_utc(value), "DATETIME"
    if isinstance(value, date):
        return datetime.combine(value, time.min, UTC), "DATE"
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None
    if _ISO_DATE_RE.fullmatch(text):
        try:
            parsed_date = date.fromisoformat(text.replace("/", "-"))
        except ValueError:
            return None
        return datetime.combine(parsed_date, time.min, UTC), "DATE"
    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed_datetime = None
    if parsed_datetime is not None:
        return _as_utc(parsed_datetime), "DATETIME"

    match = _DATE_WITH_OPTIONAL_TIME_RE.search(text)
    if match is None:
        return None
    try:
        parsed_date = date(int(match["year"]), int(match["month"]), int(match["day"]))
        if match["hour"] is None or match["minute"] is None:
            return datetime.combine(parsed_date, time.min, UTC), "DATE"
        hour = int(match["hour"])
        if match["period"] == "下午" and hour < 12:
            hour += 12
        return (
            datetime(
                parsed_date.year,
                parsed_date.month,
                parsed_date.day,
                hour,
                int(match["minute"]),
                tzinfo=UTC,
            ),
            "DATETIME",
        )
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
