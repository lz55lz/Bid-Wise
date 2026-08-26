from app.core.constants import (
    BID_SPECIALIST,
    LEGAL_COMPLIANCE,
    MATERIAL_ADMIN,
    PROJECT_OWNER,
    READ_ONLY,
    SYSTEM_ADMIN,
)
from app.core.errors import DomainError


def require_system_admin(role_codes: set[str]) -> None:
    if SYSTEM_ADMIN not in role_codes:
        raise DomainError("PERMISSION_DENIED", "无权执行该操作", 403)


def can_manage_project(role_codes: set[str], is_owner: bool) -> bool:
    return SYSTEM_ADMIN in role_codes or is_owner or PROJECT_OWNER in role_codes


def can_write_project_documents(role_codes: set[str]) -> bool:
    return bool({SYSTEM_ADMIN, PROJECT_OWNER, BID_SPECIALIST}.intersection(role_codes))


def can_manage_enterprise_materials(role_codes: set[str]) -> bool:
    return bool({SYSTEM_ADMIN, PROJECT_OWNER, MATERIAL_ADMIN}.intersection(role_codes))


def can_review_project_analysis(role_codes: set[str]) -> bool:
    return bool(
        {SYSTEM_ADMIN, PROJECT_OWNER, BID_SPECIALIST, LEGAL_COMPLIANCE}.intersection(role_codes)
    )


def can_manage_knowledge(role_codes: set[str]) -> bool:
    return bool({SYSTEM_ADMIN, LEGAL_COMPLIANCE}.intersection(role_codes))


def can_generate_reports(role_codes: set[str]) -> bool:
    return bool(
        {SYSTEM_ADMIN, PROJECT_OWNER, BID_SPECIALIST, LEGAL_COMPLIANCE}.intersection(role_codes)
    )


def can_read_reports(role_codes: set[str]) -> bool:
    return bool(
        {SYSTEM_ADMIN, PROJECT_OWNER, BID_SPECIALIST, LEGAL_COMPLIANCE, READ_ONLY}.intersection(
            role_codes
        )
    )
