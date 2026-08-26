from datetime import datetime
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from app.db.models import Enterprise, EnterpriseMember, Role, User, UserRole


class IdentityRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_user_by_username(self, username: str) -> User | None:
        return self._session.scalar(
            select(User).where(User.username == username, User.status == "ACTIVE")
        )

    def get_user(self, user_id: UUID) -> User | None:
        return self._session.scalar(select(User).where(User.id == user_id, User.status == "ACTIVE"))

    def get_user_for_management(self, user_id: UUID) -> User | None:
        return self._session.get(User, user_id)

    def list_users(self) -> list[User]:
        return list(self._session.scalars(select(User).order_by(User.created_at.desc(), User.id)))

    def list_assignable_users(self) -> list[User]:
        return list(
            self._session.scalars(
                select(User)
                .where(User.status == "ACTIVE")
                .order_by(User.display_name, User.username)
            )
        )

    def list_roles(self) -> list[Role]:
        return list(self._session.scalars(select(Role).order_by(Role.code)))

    def list_role_codes(self, user_id: UUID) -> set[str]:
        return set(
            self._session.scalars(select(UserRole.role_code).where(UserRole.user_id == user_id))
        )

    def add_user(self, user: User, role_codes: set[str]) -> User:
        self._session.add(user)
        self._session.flush()
        self._session.add_all(
            UserRole(user_id=user.id, role_code=role_code, created_at=user.created_at)
            for role_code in role_codes
        )
        return user

    def role_exists(self, role_code: str) -> bool:
        return self._session.get(Role, role_code) is not None

    def replace_roles(self, user_id: UUID, role_codes: set[str], created_at) -> None:
        self._session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        self._session.add_all(
            UserRole(user_id=user_id, role_code=role_code, created_at=created_at)
            for role_code in role_codes
        )

    # Enterprise methods
    def get_enterprise(self, enterprise_id: UUID) -> Enterprise | None:
        return self._session.get(Enterprise, enterprise_id)

    def get_enterprise_with_members(self, enterprise_id: UUID) -> Enterprise | None:
        return self._session.scalars(
            select(Enterprise)
            .options(joinedload(Enterprise.members).joinedload(EnterpriseMember.user))
            .where(Enterprise.id == enterprise_id)
        ).first()

    def list_enterprises(self, include_deleted: bool = False) -> list[Enterprise]:
        query = select(Enterprise).order_by(Enterprise.created_at.desc(), Enterprise.id)
        if not include_deleted:
            query = query.where(Enterprise.deleted_at.is_(None))
        return list(self._session.scalars(query))

    def list_user_enterprises(self, user_id: UUID) -> list[Enterprise]:
        return list(
            self._session.scalars(
                select(Enterprise)
                .join(EnterpriseMember, EnterpriseMember.enterprise_id == Enterprise.id)
                .where(
                    EnterpriseMember.user_id == user_id,
                    EnterpriseMember.status == "ACTIVE",
                    Enterprise.deleted_at.is_(None),
                )
                .order_by(Enterprise.name)
            )
        )

    def add_enterprise(self, enterprise: Enterprise) -> Enterprise:
        self._session.add(enterprise)
        self._session.flush()
        return enterprise

    def add_enterprise_member(self, member: EnterpriseMember) -> EnterpriseMember:
        self._session.add(member)
        self._session.flush()
        return member

    def get_enterprise_member(
        self, enterprise_id: UUID, user_id: UUID
    ) -> EnterpriseMember | None:
        return self._session.scalar(
            select(EnterpriseMember).where(
                EnterpriseMember.enterprise_id == enterprise_id,
                EnterpriseMember.user_id == user_id,
            )
        )

    def list_enterprise_members(self, enterprise_id: UUID) -> list[EnterpriseMember]:
        return list(
            self._session.scalars(
                select(EnterpriseMember)
                .options(joinedload(EnterpriseMember.user))
                .where(EnterpriseMember.enterprise_id == enterprise_id)
                .order_by(EnterpriseMember.created_at.desc())
            )
        )

    def update_enterprise_member(
        self, member_id: UUID, role_code: str | None = None, status: str | None = None
    ) -> EnterpriseMember | None:
        member = self._session.get(EnterpriseMember, member_id)
        if not member:
            return None
        if role_code is not None:
            member.role_code = role_code
        if status is not None:
            member.status = status
        self._session.flush()
        return member

    def delete_enterprise(self, enterprise_id: UUID) -> bool:
        enterprise = self._session.get(Enterprise, enterprise_id)
        if not enterprise:
            return False
        enterprise.deleted_at = datetime.utcnow()
        self._session.flush()
        return True
