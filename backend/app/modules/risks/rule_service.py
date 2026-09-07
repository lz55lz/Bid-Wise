"""风险规则模板的版本管理与受限 DSL 校验。

自定义模板只读取项目已确认字段，不支持表达式、函数或数据库字段名透传。这样既能
复用旧项目的 ``RuleVersion`` 能力，也不会把规则配置变成任意代码执行入口。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.modules.identity.models import AuditLog
from app.modules.identity.service import AuthenticatedUser
from app.modules.risks.rule_models import RiskRule, RiskRuleVersion
from app.modules.risks.rule_repository import RiskRuleRepository
from app.modules.risks.rule_schemas import RiskRuleTemplateResponse, RiskRuleVersionResponse
from app.modules.risks.rules import BUILTIN_RISK_RULES

SYSTEM_ADMIN = "SYSTEM_ADMIN"
LEGAL_COMPLIANCE = "LEGAL_COMPLIANCE"

_RISK_TYPES = frozenset(
    {
        "QUALIFICATION",
        "COMPLIANCE",
        "FORMAT",
        "TIME",
        "FINANCIAL",
        "TECHNICAL",
        "BUSINESS",
        "DOCUMENT",
        "DATA_QUALITY",
    }
)
_SEVERITIES = frozenset({"CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"})
_OPERATORS = frozenset(
    {
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
    }
)
_PROJECT_FIELDS = frozenset(
    {"bid_deadline", "purchaser", "project_type", "region", "status", "code", "name"}
)
_BUILTIN_CODES = frozenset(spec.code for spec in BUILTIN_RISK_RULES)


class RiskRuleTemplateService:
    """维护可复用风险模板，不处理项目级风险结果。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._rules = RiskRuleRepository(session)

    async def ensure_builtin_templates(self, actor_id: UUID) -> None:
        """首次扫描时补齐内置模板，数据库仍是启停和等级的唯一事实源。"""
        existing_codes = {rule.code for rule in await self._rules.list_rules()}
        if _BUILTIN_CODES.issubset(existing_codes):
            return
        # 两个项目首次并发做风险扫描时都可能观察到“模板不存在”。只有确实缺模板
        # 时才使用事务级 advisory lock；正常风险扫描不会被全局串行化。
        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext('bidwise:risk-rule-builtins'))")
        )
        now = datetime.now(UTC)
        for spec in BUILTIN_RISK_RULES:
            if await self._rules.get_by_code(spec.code) is not None:
                continue
            rule = RiskRule(
                id=uuid4(),
                code=spec.code,
                name=spec.name,
                risk_type=spec.risk_type,
                created_at=now,
                created_by=actor_id,
            )
            self._rules.add_rule(rule)
            self._rules.add_version(
                RiskRuleVersion(
                    id=uuid4(),
                    rule_id=rule.id,
                    version_no=1,
                    severity=spec.severity,
                    definition=spec.definition,
                    is_enabled=True,
                    effective_at=now,
                    retired_at=None,
                    created_at=now,
                    created_by=actor_id,
                )
            )

    async def list(self, actor: AuthenticatedUser) -> list[RiskRuleTemplateResponse]:
        self._require_manager(actor)
        active_versions = {
            rule.id: version for rule, version in await self._rules.list_active_versions()
        }
        return [
            self._response(rule, active_versions.get(rule.id))
            for rule in await self._rules.list_rules()
        ]

    async def create(
        self,
        actor: AuthenticatedUser,
        *,
        code: str,
        name: str,
        risk_type: str,
        severity: str,
        definition: dict[str, object],
        is_enabled: bool,
    ) -> RiskRuleTemplateResponse:
        self._require_manager(actor)
        normalized_code = code.upper()
        if normalized_code in _BUILTIN_CODES:
            raise DomainError("RULE_RESERVED", "内置风险模板不能以新建方式覆盖", 409)
        self._validate_rule_fields(risk_type, severity)
        self._validate_definition(definition, builtin=False)
        if await self._rules.get_by_code(normalized_code) is not None:
            raise DomainError("RESOURCE_CONFLICT", "风险规则编码已存在", 409)
        now = datetime.now(UTC)
        rule = RiskRule(
            id=uuid4(),
            code=normalized_code,
            name=name,
            risk_type=risk_type,
            created_at=now,
            created_by=actor.id,
        )
        self._rules.add_rule(rule)
        self._rules.add_version(
            RiskRuleVersion(
                id=uuid4(),
                rule_id=rule.id,
                version_no=1,
                severity=severity,
                definition=definition,
                is_enabled=is_enabled,
                effective_at=now,
                retired_at=None,
                created_at=now,
                created_by=actor.id,
            )
        )
        self._record_audit(actor.id, "CREATE_RISK_RULE_TEMPLATE", rule.id, f"{rule.code} v1")
        await self._session.flush()
        version = await self._rules.get_active_version(rule.id)
        return self._response(rule, version)

    async def create_version(
        self,
        actor: AuthenticatedUser,
        rule_id: UUID,
        *,
        name: str | None,
        risk_type: str | None,
        severity: str,
        definition: dict[str, object],
        is_enabled: bool,
    ) -> RiskRuleTemplateResponse:
        self._require_manager(actor)
        rule = await self._rules.get_rule(rule_id, for_update=True)
        if rule is None:
            raise DomainError("RESOURCE_NOT_FOUND", "风险规则模板不存在", 404)
        self._validate_rule_fields(risk_type or rule.risk_type, severity)
        self._validate_definition(definition, builtin=rule.code in _BUILTIN_CODES)
        previous = await self._rules.get_active_version(rule.id)
        now = datetime.now(UTC)
        if previous is not None:
            previous.is_enabled = False
            previous.retired_at = now
        if name is not None:
            rule.name = name
        if risk_type is not None:
            rule.risk_type = risk_type
        version = RiskRuleVersion(
            id=uuid4(),
            rule_id=rule.id,
            version_no=await self._rules.next_version_no(rule.id),
            severity=severity,
            definition=definition,
            is_enabled=is_enabled,
            effective_at=now,
            retired_at=None,
            created_at=now,
            created_by=actor.id,
        )
        self._rules.add_version(version)
        self._record_audit(
            actor.id,
            "VERSION_RISK_RULE_TEMPLATE",
            rule.id,
            f"{rule.code} v{version.version_no}",
        )
        await self._session.flush()
        return self._response(rule, version if version.is_enabled else None)

    @classmethod
    def _validate_rule_fields(cls, risk_type: str, severity: str) -> None:
        if risk_type not in _RISK_TYPES or severity not in _SEVERITIES:
            raise DomainError("VALIDATION_ERROR", "风险类型或风险等级不合法", 422)

    @classmethod
    def _validate_definition(cls, definition: object, *, builtin: bool) -> None:
        required_keys = {"all", "message_template", "evidence_selector"}
        if not isinstance(definition, dict) or set(definition) != required_keys:
            raise DomainError(
                "VALIDATION_ERROR",
                "规则定义必须包含 all、message_template、evidence_selector",
                422,
            )
        conditions = definition["all"]
        if not isinstance(conditions, list) or not 1 <= len(conditions) <= 32:
            raise DomainError("VALIDATION_ERROR", "规则至少需要一条且最多 32 条条件", 422)
        message_template = definition["message_template"]
        if not isinstance(message_template, str) or not message_template.strip():
            raise DomainError("VALIDATION_ERROR", "规则必须包含风险提示文案", 422)
        evidence_selector = definition["evidence_selector"]
        if not isinstance(evidence_selector, dict) or not evidence_selector:
            raise DomainError("VALIDATION_ERROR", "规则必须包含证据选择器", 422)
        for condition in conditions:
            if not isinstance(condition, dict) or not {"source", "field", "op"}.issubset(condition):
                raise DomainError("VALIDATION_ERROR", "规则条件格式不合法", 422)
            if set(condition).difference({"source", "field", "op", "value"}):
                raise DomainError("VALIDATION_ERROR", "规则条件包含未支持字段", 422)
            source, field, operator = condition["source"], condition["field"], condition["op"]
            if source not in {"project", "requirement", "material"} or not isinstance(field, str):
                raise DomainError("VALIDATION_ERROR", "规则条件来源或字段不合法", 422)
            if not builtin and source != "project":
                raise DomainError(
                    "VALIDATION_ERROR",
                    "自定义模板仅支持项目字段，材料/需求规则必须由内置执行器维护",
                    422,
                )
            if source == "project" and field not in _PROJECT_FIELDS:
                raise DomainError("VALIDATION_ERROR", "不支持的项目字段", 422)
            if operator not in _OPERATORS:
                raise DomainError("VALIDATION_ERROR", "规则操作符不受支持", 422)
            if operator == "DATE_BEFORE" and not builtin:
                raise DomainError("VALIDATION_ERROR", "DATE_BEFORE 仅供内置材料规则使用", 422)
            if operator not in {"EXISTS", "NOT_EXISTS", "LT_NOW"} and "value" not in condition:
                raise DomainError("VALIDATION_ERROR", "规则条件缺少比较值", 422)
            if "value" in condition:
                cls._validate_value(condition["value"])

    @staticmethod
    def _validate_value(value: object) -> None:
        if value is None or isinstance(value, bool | int | float | str):
            return
        if isinstance(value, list) and all(
            item is None or isinstance(item, bool | int | float | str) for item in value
        ):
            return
        if (
            isinstance(value, dict)
            and set(value) == {"source", "field"}
            and value.get("source") == "project"
            and value.get("field") in _PROJECT_FIELDS
        ):
            return
        raise DomainError("VALIDATION_ERROR", "规则比较值不合法", 422)

    @staticmethod
    def _require_manager(actor: AuthenticatedUser) -> None:
        if not {SYSTEM_ADMIN, LEGAL_COMPLIANCE}.intersection(actor.role_codes):
            raise DomainError("PERMISSION_DENIED", "无权维护风险规则模板", 403)

    def _record_audit(self, actor_id: UUID, action: str, rule_id: UUID, summary: str) -> None:
        self._session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                target_type="RISK_RULE",
                target_id=rule_id,
                project_id=None,
                before_summary=None,
                after_summary=summary,
                created_at=datetime.now(UTC),
            )
        )

    @staticmethod
    def _response(rule: RiskRule, version: RiskRuleVersion | None) -> RiskRuleTemplateResponse:
        return RiskRuleTemplateResponse(
            id=rule.id,
            code=rule.code,
            name=rule.name,
            risk_type=rule.risk_type,
            active_version=None
            if version is None
            else RiskRuleVersionResponse(
                id=version.id,
                version_no=version.version_no,
                severity=version.severity,
                definition=version.definition,
                is_enabled=version.is_enabled,
                effective_at=version.effective_at,
                retired_at=version.retired_at,
                created_at=version.created_at,
                created_by=version.created_by,
            ),
        )
