"""初始化首个系统管理员。

运行命令时通过终端交互读取密码，避免默认口令、命令历史和仓库文件泄漏凭据。
该命令只允许在 users 表为空时执行一次。
"""

import argparse
import asyncio
import getpass
import re

from app.core.errors import DomainError
from app.db.session import get_session_factory
from app.modules.identity.service import IdentityAdministrationService

_username_pattern = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")


def parse_arguments() -> argparse.Namespace:
    """只读取不敏感账号信息；密码始终由 getpass 交互输入。"""
    parser = argparse.ArgumentParser(description="初始化 Bid-Wise 首个系统管理员")
    parser.add_argument("--username", required=True, help="登录用户名，仅支持小写字母、数字和 ._- ")
    parser.add_argument("--display-name", required=True, help="页面显示名称")
    return parser.parse_args()


async def bootstrap(username: str, display_name: str, password: str) -> None:
    """创建管理员并提交；任何异常都由 Session 上下文自动回滚。"""
    async with get_session_factory()() as session:
        try:
            user = await IdentityAdministrationService(session).bootstrap_system_admin(
                username,
                password,
                display_name,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    print(f"已初始化系统管理员：{user.username}")


def main() -> None:
    """校验输入、两次确认密码后执行异步初始化。"""
    arguments = parse_arguments()
    username = arguments.username.strip().lower()
    display_name = arguments.display_name.strip()
    if not _username_pattern.fullmatch(username):
        raise SystemExit("用户名只能包含小写字母、数字、点、下划线或连字符")
    if not display_name:
        raise SystemExit("显示名称不能为空")
    password = getpass.getpass("管理员密码（至少 12 位）: ")
    if password != getpass.getpass("再次输入管理员密码: "):
        raise SystemExit("两次输入的密码不一致")
    try:
        asyncio.run(bootstrap(username, display_name, password))
    except DomainError as exc:
        raise SystemExit(exc.message) from exc


if __name__ == "__main__":
    main()
