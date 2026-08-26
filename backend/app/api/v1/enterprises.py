from uuid import UUID

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.constants import SYSTEM_ADMIN
from app.db.repositories.identity_repository import IdentityRepository
from app.db.session import get_db_session
from app.schemas.enterprise import (
    EnterpriseCreate,
    EnterpriseMemberCreate,
    EnterpriseMemberResponse,
    EnterpriseMemberUpdate,
    EnterpriseResponse,
    EnterpriseUpdate,
    EnterpriseWithMembersResponse,
)

router = APIRouter(prefix="/enterprises", tags=["enterprises"])


def get_repo(session: Session) -> IdentityRepository:
    return IdentityRepository(session)


@router.get("", response_model=list[EnterpriseResponse])
def list_enterprises(
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[EnterpriseResponse]:
    """获取当前用户所属的所有企业"""
    from sqlalchemy import text
    # 查 enterprises 表获取用户关联的企业
    result = session.execute(
        text("""
            SELECT e.id, e.name, e.credit_code, e.enterprise_type, e.status,
                   e.created_at, e.created_by,
                   ep.qualifications, ep.past_projects, ep.financials,
                   ep.personnel, ep.awards, ep.blacklist_status
            FROM app.enterprises e
            LEFT JOIN enterprise_profile ep ON ep.enterprise_id = e.id::varchar
            LEFT JOIN app.enterprise_members m ON m.enterprise_id = e.id AND m.user_id = :uid AND m.status = 'ACTIVE'
            WHERE m.user_id = :uid OR :is_admin = true
            ORDER BY e.created_at DESC
        """),
        {"uid": str(current_user.id), "is_admin": SYSTEM_ADMIN in current_user.role_codes},
    )
    rows = result.fetchall()
    return [
        EnterpriseResponse(
            id=r[0],
            name=r[1],
            credit_code=r[2],
            enterprise_type=r[3] or "投标企业",
            status=r[4] or "ACTIVE",
            created_at=r[5],
            created_by=r[6],
            qualifications=r[7],
            past_projects=r[8],
            financials=r[9],
            personnel=r[10],
            awards=r[11],
            blacklist_status=r[12],
        )
        for r in rows
    ]


@router.post("", response_model=EnterpriseResponse, status_code=status.HTTP_201_CREATED)
def create_enterprise(
    payload: EnterpriseCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseResponse:
    """创建新企业，创建者自动成为企业管理员"""
    from datetime import datetime
    from uuid import uuid4

    from app.db.models import Enterprise, EnterpriseMember, EnterpriseProfile

    repo = get_repo(session)

    enterprise = Enterprise(
        id=uuid4(),
        name=payload.name,
        credit_code=payload.credit_code,
        enterprise_type=payload.enterprise_type,
        created_at=datetime.utcnow(),
        created_by=current_user.id,
        updated_at=datetime.utcnow(),
    )
    repo.add_enterprise(enterprise)

    # 同步到 enterprise_profile
    ep = EnterpriseProfile(
        enterprise_id=str(enterprise.id),
        enterprise_name=payload.name,
        credit_code=payload.credit_code,
        enterprise_type=payload.enterprise_type or "投标企业",
        status="ACTIVE",
        created_by=str(current_user.id),
        created_at=datetime.utcnow(),
    )
    session.add(ep)

    # 创建者自动成为企业管理员
    member = EnterpriseMember(
        id=uuid4(),
        enterprise_id=enterprise.id,
        user_id=current_user.id,
        role_code="ADMIN",
        status="ACTIVE",
        created_at=datetime.utcnow(),
    )
    repo.add_enterprise_member(member)

    return EnterpriseResponse.model_validate(enterprise, from_attributes=True)


@router.get("/{enterprise_id}", response_model=EnterpriseWithMembersResponse)
def get_enterprise(
    enterprise_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseWithMembersResponse:
    """获取企业详情（含成员列表）"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否属于该企业
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership and SYSTEM_ADMIN not in current_user.role_codes:
        raise DomainError("PERMISSION_DENIED", "无权访问该企业", 403)

    enterprise = repo.get_enterprise_with_members(enterprise_id)
    if not enterprise:
        raise DomainError("NOT_FOUND", "企业不存在", 404)

    # 手动构建响应以处理嵌套的 user 关系
    members = [
        EnterpriseMemberResponse(
            id=m.id,
            enterprise_id=m.enterprise_id,
            user_id=m.user_id,
            username=m.user.username if m.user else None,
            display_name=m.user.display_name if m.user else None,
            role_code=m.role_code,
            status=m.status,
            created_at=m.created_at,
        )
        for m in enterprise.members
    ]
    return EnterpriseWithMembersResponse(
        id=enterprise.id,
        name=enterprise.name,
        credit_code=enterprise.credit_code,
        enterprise_type=enterprise.enterprise_type,
        status=enterprise.status,
        created_at=enterprise.created_at,
        created_by=enterprise.created_by,
        members=members,
    )


@router.patch("/{enterprise_id}", response_model=EnterpriseResponse)
def update_enterprise(
    enterprise_id: UUID,
    payload: EnterpriseUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseResponse:
    """更新企业信息"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否为该企业管理员
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership or membership.role_code != "ADMIN":
        if SYSTEM_ADMIN not in current_user.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权修改企业", 403)

    enterprise = repo.get_enterprise(enterprise_id)
    if not enterprise:
        raise DomainError("NOT_FOUND", "企业不存在", 404)

    if payload.name is not None:
        enterprise.name = payload.name
    if payload.credit_code is not None:
        enterprise.credit_code = payload.credit_code
    if payload.enterprise_type is not None:
        enterprise.enterprise_type = payload.enterprise_type
    if payload.status is not None:
        enterprise.status = payload.status

    from datetime import datetime
    enterprise.updated_at = datetime.utcnow()
    session.flush()

    # 同步到 enterprise_profile
    from sqlalchemy import text
    session.execute(
        text("""
            UPDATE enterprise_profile
            SET enterprise_name = :name, credit_code = :code
            WHERE enterprise_id = :eid
        """),
        {"name": enterprise.name, "code": enterprise.credit_code, "eid": str(enterprise.id)},
    )

    return EnterpriseResponse.model_validate(enterprise, from_attributes=True)


@router.delete("/{enterprise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_enterprise(
    enterprise_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """删除企业（软删除）"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否为该企业管理员
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership or membership.role_code != "ADMIN":
        if SYSTEM_ADMIN not in current_user.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权删除企业", 403)

    if not repo.delete_enterprise(enterprise_id):
        raise DomainError("NOT_FOUND", "企业不存在", 404)

    # 同步删除 enterprise_profile
    from sqlalchemy import text
    session.execute(
        text("DELETE FROM enterprise_profile WHERE enterprise_id = :eid"),
        {"eid": str(enterprise_id)},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{enterprise_id}/members", response_model=list[EnterpriseMemberResponse])
def list_members(
    enterprise_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> list[EnterpriseMemberResponse]:
    """获取企业成员列表"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否属于该企业
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership and SYSTEM_ADMIN not in current_user.role_codes:
        raise DomainError("PERMISSION_DENIED", "无权访问该企业", 403)

    enterprise = repo.get_enterprise(enterprise_id)
    if not enterprise:
        raise DomainError("NOT_FOUND", "企业不存在", 404)

    members = repo.list_enterprise_members(enterprise_id)
    return [
        EnterpriseMemberResponse(
            id=m.id,
            enterprise_id=m.enterprise_id,
            user_id=m.user_id,
            username=m.user.username if m.user else None,
            display_name=m.user.display_name if m.user else None,
            role_code=m.role_code,
            status=m.status,
            created_at=m.created_at,
        )
        for m in members
    ]


@router.post("/{enterprise_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    enterprise_id: UUID,
    payload: EnterpriseMemberCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseMemberResponse:
    """添加企业成员"""
    from datetime import datetime
    from uuid import uuid4

    from app.core.errors import DomainError
    from app.db.models import EnterpriseMember

    repo = get_repo(session)

    # 验证用户是否为该企业管理员
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership or membership.role_code != "ADMIN":
        if SYSTEM_ADMIN not in current_user.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权添加成员", 403)

    enterprise = repo.get_enterprise(enterprise_id)
    if not enterprise:
        raise DomainError("NOT_FOUND", "企业不存在", 404)

    # 检查是否已是成员
    existing = repo.get_enterprise_member(enterprise_id, payload.user_id)
    if existing:
        raise DomainError("ALREADY_EXISTS", "该用户已是企业成员", 400)

    member = EnterpriseMember(
        id=uuid4(),
        enterprise_id=enterprise_id,
        user_id=payload.user_id,
        role_code=payload.role_code,
        status="ACTIVE",
        created_at=datetime.utcnow(),
    )
    repo.add_enterprise_member(member)

    # 获取用户信息用于响应
    from app.db.repositories.identity_repository import IdentityRepository
    user_repo = IdentityRepository(session)
    user = user_repo.get_user(payload.user_id)
    return EnterpriseMemberResponse(
        id=member.id,
        enterprise_id=member.enterprise_id,
        user_id=member.user_id,
        username=user.username if user else None,
        display_name=user.display_name if user else None,
        role_code=member.role_code,
        status=member.status,
        created_at=member.created_at,
    )


@router.patch("/{enterprise_id}/members/{member_id}", response_model=EnterpriseMemberResponse)
def update_member(
    enterprise_id: UUID,
    member_id: UUID,
    payload: EnterpriseMemberUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> EnterpriseMemberResponse:
    """更新企业成员"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否为该企业管理员
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership or membership.role_code != "ADMIN":
        if SYSTEM_ADMIN not in current_user.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权修改成员", 403)

    member = repo.update_enterprise_member(member_id, payload.role_code, payload.status)
    if not member or member.enterprise_id != enterprise_id:
        raise DomainError("NOT_FOUND", "成员不存在", 404)

    # 获取用户信息用于响应
    from app.db.repositories.identity_repository import IdentityRepository
    user_repo = IdentityRepository(session)
    user = user_repo.get_user(member.user_id)
    return EnterpriseMemberResponse(
        id=member.id,
        enterprise_id=member.enterprise_id,
        user_id=member.user_id,
        username=user.username if user else None,
        display_name=user.display_name if user else None,
        role_code=member.role_code,
        status=member.status,
        created_at=member.created_at,
    )


@router.delete("/{enterprise_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    enterprise_id: UUID,
    member_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> Response:
    """移除企业成员"""
    from app.core.errors import DomainError

    repo = get_repo(session)

    # 验证用户是否为该企业管理员
    membership = repo.get_enterprise_member(enterprise_id, current_user.id)
    if not membership or membership.role_code != "ADMIN":
        if SYSTEM_ADMIN not in current_user.role_codes:
            raise DomainError("PERMISSION_DENIED", "无权移除成员", 403)

    member = repo.update_enterprise_member(member_id, status="REMOVED")
    if not member or member.enterprise_id != enterprise_id:
        raise DomainError("NOT_FOUND", "成员不存在", 404)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
