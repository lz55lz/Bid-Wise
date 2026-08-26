from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.constants import LEGAL_COMPLIANCE, SYSTEM_ADMIN
from app.core.errors import DomainError
from app.db.models import Rule, RuleVersion
from app.db.repositories.risk_repository import RuleRepository
from app.schemas.rules import (
    RuleCreateRequest,
    RuleResponse,
    RuleVersionRequest,
    RuleVersionResponse,
)
from app.services.audit_service import AuditService

_BUILTIN_RULE_CODES = frozenset(
    {
        "DEADLINE_EXPIRED",
        "CERTIFICATE_EXPIRED",
        "QUANTITATIVE_REQUIREMENT_UNMET",
        "MANDATORY_EVIDENCE_MISSING",
    }
)
_ALLOWED_OPERATORS = {
    "EQ",
    "NE",
    "GT",
    "GTE",
    "LT",
    "LTE",
    "IN",
    "EXISTS",
    "NOT_EXISTS",
    "LT_NOW",
    "DATE_BEFORE",
    "COUNT_LT",
}
_PROJECT_FIELDS = {
    "bid_deadline",
    "budget",
    "max_price",
    "status",
    "purchaser",
    "project_type",
    "region",
}


class RuleService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._rules = RuleRepository(session)
        self._audit = AuditService(session)

    def list(self, role_codes: set[str]) -> list[RuleResponse]:
        self._require_manager(role_codes)
        return [self._response(rule) for rule in self._rules.list_rules()]

    def create(
        self, actor_id: UUID, role_codes: set[str], payload: RuleCreateRequest
    ) -> RuleResponse:
        self._require_manager(role_codes)
        code = payload.code.upper()
        if code in _BUILTIN_RULE_CODES:
            raise DomainError("RULE_RESERVED", "P0 内置规则只能通过新版本调整。", 409)
        self._validate_definition(payload.definition, allow_non_project=False)
        if self._rules.get_by_code(code) is not None:
            self._session.rollback()
            raise DomainError("RESOURCE_CONFLICT", "规则编码已存在。", 409)
        now = datetime.now(UTC)
        rule = Rule(
            id=uuid4(),
            code=code,
            name=payload.name,
            risk_type=payload.risk_type,
            created_at=now,
            created_by=actor_id,
        )
        version = RuleVersion(
            id=uuid4(),
            rule_id=rule.id,
            version_no=1,
            severity=payload.severity,
            definition=payload.definition,
            is_enabled=payload.is_enabled,
            effective_at=now,
            retired_at=None,
            created_at=now,
            created_by=actor_id,
        )
        self._rules.add_rule(rule)
        self._rules.add_version(version)
        self._audit.record(
            actor_id=actor_id,
            action="CREATE_RULE",
            target_type="RULE",
            target_id=rule.id,
            after={"code": rule.code, "version_no": version.version_no},
        )
        try:
            self._session.commit()
        except IntegrityError:
            self._session.rollback()
            raise DomainError("RESOURCE_CONFLICT", "规则编码已存在。", 409) from None
        return self._response(rule, version)

    def version(
        self,
        rule_id: UUID,
        actor_id: UUID,
        role_codes: set[str],
        payload: RuleVersionRequest,
    ) -> RuleResponse:
        self._require_manager(role_codes)
        rule = self._rules.get(rule_id, for_update=True)
        if rule is None:
            raise DomainError("RESOURCE_NOT_FOUND", "规则不存在。", 404)
        self._validate_definition(
            payload.definition, allow_non_project=rule.code in _BUILTIN_RULE_CODES
        )
        previous = self._rules.get_active_version(rule.id)
        now = datetime.now(UTC)
        if previous is not None:
            previous.is_enabled = False
            previous.retired_at = now
        if payload.name is not None:
            rule.name = payload.name
        if payload.risk_type is not None:
            rule.risk_type = payload.risk_type
        version = RuleVersion(
            id=uuid4(),
            rule_id=rule.id,
            version_no=self._rules.next_version_no(rule.id),
            severity=payload.severity,
            definition=payload.definition,
            is_enabled=payload.is_enabled,
            effective_at=now,
            retired_at=None,
            created_at=now,
            created_by=actor_id,
        )
        self._rules.add_version(version)
        self._audit.record(
            actor_id=actor_id,
            action="VERSION_RULE",
            target_type="RULE",
            target_id=rule.id,
            before={"version_no": None if previous is None else previous.version_no},
            after={"version_no": version.version_no, "is_enabled": version.is_enabled},
        )
        self._session.commit()
        return self._response(rule, version if version.is_enabled else None)

    def _response(self, rule: Rule, version: RuleVersion | None = None) -> RuleResponse:
        active = version if version is not None else self._rules.get_active_version(rule.id)
        return RuleResponse(
            id=rule.id,
            code=rule.code,
            name=rule.name,
            risk_type=rule.risk_type,
            active_version=None if active is None else self._version_response(active),
        )

    @staticmethod
    def _version_response(version: RuleVersion) -> RuleVersionResponse:
        return RuleVersionResponse(
            id=version.id,
            version_no=version.version_no,
            severity=version.severity,
            definition=version.definition,
            is_enabled=version.is_enabled,
            effective_at=version.effective_at,
            retired_at=version.retired_at,
            created_at=version.created_at,
            created_by=version.created_by,
        )

    @classmethod
    def _validate_definition(cls, value: object, *, allow_non_project: bool) -> None:
        if not isinstance(value, dict) or set(value) != {
            "all",
            "message_template",
            "evidence_selector",
        }:
            raise DomainError("VALIDATION_ERROR", "规则定义字段不合法。", 422)
        all_conditions = value.get("all")
        if not isinstance(all_conditions, list) or not 1 <= len(all_conditions) <= 32:
            raise DomainError("VALIDATION_ERROR", "规则至少需要一条且最多 32 条条件。", 422)
        if (
            not isinstance(value.get("message_template"), str)
            or not value["message_template"].strip()
        ):
            raise DomainError("VALIDATION_ERROR", "规则必须包含风险提示文案。", 422)
        selector = value.get("evidence_selector")
        if not isinstance(selector, dict) or not selector:
            raise DomainError("VALIDATION_ERROR", "规则必须包含证据选择器。", 422)
        for condition in all_conditions:
            if not isinstance(condition, dict) or not {"source", "field", "op"}.issubset(condition):
                raise DomainError("VALIDATION_ERROR", "规则条件格式不合法。", 422)
            if set(condition) - {"source", "field", "op", "value"}:
                raise DomainError("VALIDATION_ERROR", "规则条件包含未支持字段。", 422)
            source, field, operator = (
                condition["source"],
                condition["field"],
                condition["op"],
            )
            if not isinstance(source, str) or source not in {"project", "requirement", "material"}:
                raise DomainError("VALIDATION_ERROR", "规则条件来源不合法。", 422)
            if not allow_non_project and source != "project":
                raise DomainError("VALIDATION_ERROR", "自定义规则仅支持项目字段。", 422)
            if (
                not isinstance(field, str)
                or len(field) > 80
                or not field.replace("_", "").isalnum()
            ):
                raise DomainError("VALIDATION_ERROR", "规则字段不合法。", 422)
            if source == "project" and field not in _PROJECT_FIELDS:
                raise DomainError("VALIDATION_ERROR", "不支持的项目规则字段。", 422)
            if not isinstance(operator, str) or operator not in _ALLOWED_OPERATORS:
                raise DomainError("VALIDATION_ERROR", "规则操作符不受支持。", 422)
            if operator not in {"EXISTS", "NOT_EXISTS", "LT_NOW"} and "value" not in condition:
                raise DomainError("VALIDATION_ERROR", "规则条件缺少比较值。", 422)
            if "value" in condition:
                cls._validate_condition_value(condition["value"])

    @staticmethod
    def _validate_condition_value(value: object) -> None:
        if value is None or isinstance(value, (bool, int, float, str)):
            return
        if isinstance(value, list) and all(
            item is None or isinstance(item, (bool, int, float, str)) for item in value
        ):
            return
        if isinstance(value, dict) and set(value) == {"source", "field"}:
            source, field = value["source"], value["field"]
            if source == "project" and isinstance(field, str) and field in _PROJECT_FIELDS:
                return
        raise DomainError("VALIDATION_ERROR", "规则比较值不合法。", 422)

    @staticmethod
    def _require_manager(role_codes: set[str]) -> None:
        if not {SYSTEM_ADMIN, LEGAL_COMPLIANCE}.intersection(role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护规则。", 403)
