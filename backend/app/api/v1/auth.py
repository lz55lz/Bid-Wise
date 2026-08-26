"""Authentication and user management API.

Handles login/logout, JWT token lifecycle, user CRUD, role management,
and WeCom OAuth single sign-on (SSO).
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.core.permissions import require_system_admin
from app.core.security import revoke_access_token
from app.db.session import get_db_session
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    ManagedUserResponse,
    RoleResponse,
    UserCreate,
    UserResponse,
    UserRoleUpdate,
)
from app.services.auth_service import AuthService

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
def login(
    # Authenticate with username and password.
    # Returns JWT access token on success.
    payload: LoginRequest,
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> LoginResponse:
    return AuthService(session, settings).login(payload.username, payload.password)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    # Revoke current JWT token.
    current_user: CurrentUser = Depends(get_current_user),
) -> None:
    revoke_access_token(current_user.token_id)


@router.get("/me", response_model=UserResponse)
def get_me(
    # Get current authenticated user profile.
    current_user: CurrentUser = Depends(get_current_user),
) -> UserResponse:
    return UserResponse(
        id=current_user.id,
        username=current_user.username,
        display_name=current_user.display_name,
        roles=sorted(current_user.role_codes),
    )


@router.post("/users", response_model=ManagedUserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    # Create a new user account (system admin only).
    payload: UserCreate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ManagedUserResponse:
    require_system_admin(current_user.role_codes)
    return AuthService(session, settings).create_user(current_user.id, payload)


@router.get("/users", response_model=list[ManagedUserResponse])
def list_users(
    # List all managed users (system admin only).
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[ManagedUserResponse]:
    require_system_admin(current_user.role_codes)
    return AuthService(session, settings).list_users()


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    # List all available system roles (system admin only).
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> list[RoleResponse]:
    require_system_admin(current_user.role_codes)
    return AuthService(session, settings).list_roles()


@router.patch("/users/{user_id}", response_model=ManagedUserResponse)
def update_user_roles(
    # Update user roles and status (system admin only).
    user_id: UUID,
    payload: UserRoleUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> ManagedUserResponse:
    require_system_admin(current_user.role_codes)
    return AuthService(session, settings).update_user_roles(current_user.id, user_id, payload)


# ── WeCom OAuth Login ──────────────────────────────────────────────────────────
# Enterprise WeChat (wecom) OAuth2 QR code login.
# New users are auto-registered; existing users are auto-logged in.

import urllib.parse

from fastapi.responses import RedirectResponse

from app.integrations.im.adapters import WeComAdapter


@router.get("/auth/wecom/qr")
def wecom_oauth_qr(
    # Generate WeCom OAuth2 QR login URL.
    # New users are auto-registered after scanning; old users auto-login.
    # Callback handled by /auth/wecom/callback.
    redirect_uri: str = Query(..., description="Callback URL after QR scan"),
    session: Session = Depends(get_db_session),
) -> dict:
    import uuid

    from sqlalchemy import select

    from app.db.models.im_channel import IMChannel

    channel = session.execute(
        select(IMChannel).where(IMChannel.platform == "wecom", IMChannel.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if not channel or not channel.enabled:
        raise DomainError("WECOM_NOT_CONFIGURED", "WeCom channel not configured", 400)

    creds = channel.credentials or {}
    corp_id = creds.get("corp_id", "")
    agent_id = str(creds.get("corp_agent_id", ""))

    if not corp_id or not agent_id:
        raise DomainError("WECOM_NOT_CONFIGURED", "WeCom credentials incomplete", 400)

    state = uuid.uuid4().hex
    params = urllib.parse.urlencode({
        "login_type": "corporate_app",
        "appid": corp_id,
        "agentid": agent_id,
        "redirect_uri": redirect_uri,
        "state": state,
    })
    oauth_url = f"https://login.work.weixin.qq.com/wwlogin/sso/login?{params}"
    return {"oauth_url": oauth_url, "state": state}


@router.get("/auth/wecom/callback")
def wecom_oauth_callback(
    # WeCom OAuth2 callback.
    # Exchange code for user_id, auto-register or bind user, return JWT.
    code: str = Query(...),
    state: str = Query(...),
    session: Session = Depends(get_db_session),
    settings: Settings = Depends(get_settings),
) -> RedirectResponse:
    from uuid import uuid4

    from sqlalchemy import select

    from app.db.models.identity import User

    # Get WeCom channel credentials
    from app.db.models.im_channel import IMChannel
    from app.db.models.oauth import SysLoginLog, SysUserOauth

    channel = session.execute(
        select(IMChannel).where(IMChannel.platform == "wecom", IMChannel.deleted_at.is_(None)).limit(1)
    ).scalar_one_or_none()
    if not channel:
        raise DomainError("WECOM_NOT_CONFIGURED", "WeCom channel not configured", 400)

    import asyncio

    adapter = WeComAdapter(channel.credentials)
    info = asyncio.run(adapter.get_userinfo_by_code(code))
    wecom_user_id = info.get("userid") or info.get("openid")
    if not wecom_user_id:
        raise DomainError("WECOM_OAUTH_FAILED", "Cannot get WeCom user identity", 400)

    corp_id = channel.credentials.get("corp_id", "")

    # Check if OAuth binding record already exists
    oauth_record = session.execute(
        select(SysUserOauth).where(
            SysUserOauth.corp_id == corp_id,
            SysUserOauth.wecom_user_id == wecom_user_id,
        )
    ).scalar_one_or_none()

    is_new_user = False
    if oauth_record:
        # Existing user: look up by id
        user = session.get(User, oauth_record.user_id)
    else:
        # New user: auto-register
        from app.core.security import hash_password

        user = User(
            id=uuid4(),
            username=f"wecom_{wecom_user_id}",
            password_hash=hash_password(uuid4().hex[:16]),
            display_name=f"WeCom_{wecom_user_id[:8]}",
        )
        session.add(user)
        session.flush()

        # Create OAuth binding record
        oauth_record = SysUserOauth(
            user_id=user.id,
            corp_id=corp_id,
            wecom_user_id=wecom_user_id,
        )
        session.add(oauth_record)
        is_new_user = True

    # Write login audit log
    login_log = SysLoginLog(
        user_id=user.id if user else None,
        login_type="wecom_qrcode",
        status=1,
        msg="Login success",
    )
    session.add(login_log)
    session.commit()

    # Issue JWT
    from app.core.security import create_access_token

    token, _, _ = create_access_token(
        str(user.id),
        settings.jwt_secret_key.get_secret_value(),
        settings.jwt_access_token_minutes,
    )

    # Redirect to frontend with token
    fragment = f"token={token}&new_user={str(is_new_user).lower()}"
    return RedirectResponse(url=f"/?{fragment}", status_code=302)
