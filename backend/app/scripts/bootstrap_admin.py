"""通过交互式终端创建首位系统管理员，绝不将密码写入命令历史。"""

from getpass import getpass

from app.core.config import get_settings
from app.core.constants import SYSTEM_ADMIN
from app.core.errors import DomainError
from app.db.session import get_session_factory
from app.schemas.auth import UserCreate
from app.services.auth_service import AuthService


def main() -> None:
    username = input("管理员用户名: ").strip().lower()
    password = getpass("管理员密码: ")
    confirmation = getpass("再次输入密码: ")
    if password != confirmation:
        raise SystemExit("两次输入的密码不一致")

    settings = get_settings()
    session = get_session_factory()()
    try:
        user = AuthService(session, settings).create_user(
            None,
            UserCreate(
                username=username,
                password=password,
                display_name="系统管理员",
                roles={SYSTEM_ADMIN},
            ),
        )
    except DomainError as exc:
        session.rollback()
        raise SystemExit(exc.message) from exc
    finally:
        session.close()
    print(f"已创建系统管理员：{user.username}")


if __name__ == "__main__":
    main()
