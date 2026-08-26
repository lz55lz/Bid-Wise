import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.deps import get_db_session
from app.core.config import Settings, get_settings
from app.core.constants import BID_SPECIALIST, SYSTEM_ADMIN
from app.core.security import create_access_token, hash_password
from app.db.models import User, UserRole
from app.main import create_app

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_real_postgres_auth_project_authorization_and_audit() -> None:
    engine = create_engine(DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    app = create_app()
    settings = Settings(
        database_url=DATABASE_URL,
        jwt_secret_key="integration-test-signing-key-at-least-32-bytes",
    )
    now = datetime.now(UTC)
    admin = User(
        username=f"admin-{uuid4().hex[:12]}",
        password_hash=hash_password("integration-test-password"),
        display_name="集成测试管理员",
        created_at=now,
        updated_at=now,
    )
    outsider = User(
        username=f"outsider-{uuid4().hex[:12]}",
        password_hash=hash_password("integration-test-password"),
        display_name="集成测试非成员",
        created_at=now,
        updated_at=now,
    )
    try:
        session.add_all([admin, outsider])
        session.flush()
        session.add_all(
            [
                UserRole(user_id=admin.id, role_code=SYSTEM_ADMIN, created_at=now),
                UserRole(user_id=outsider.id, role_code=BID_SPECIALIST, created_at=now),
            ]
        )
        session.commit()
        app.dependency_overrides[get_db_session] = lambda: session
        app.dependency_overrides[get_settings] = lambda: settings
        client = TestClient(app)

        login = client.post(
            "/api/v1/auth/login",
            json={"username": admin.username, "password": "integration-test-password"},
        )
        assert login.status_code == 200
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        create = client.post(
            "/api/v1/projects",
            headers=headers,
            json={
                "name": "集成测试项目",
                "code": f"IT-{uuid4().hex[:12]}",
                "purchaser": "测试招标人",
                "project_type": "服务",
                "region": "上海",
                "bid_deadline": (now + timedelta(days=7)).isoformat(),
            },
        )
        assert create.status_code == 201
        project_id = create.json()["id"]
        audits = client.get("/api/v1/audit-logs", headers=headers)
        assert audits.status_code == 200
        assert any(item["action"] == "CREATE_PROJECT" for item in audits.json())

        outsider_token, _, _ = create_access_token(
            str(outsider.id), "integration-test-signing-key-at-least-32-bytes", 30
        )
        forbidden = client.get(
            f"/api/v1/projects/{project_id}",
            headers={"Authorization": f"Bearer {outsider_token}"},
        )
        assert forbidden.status_code == 404
    finally:
        app.dependency_overrides.clear()
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
