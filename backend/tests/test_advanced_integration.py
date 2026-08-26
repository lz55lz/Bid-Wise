import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.constants import BID_SPECIALIST, SYSTEM_ADMIN
from app.core.security import hash_password
from app.db.models import (
    Document,
    DocumentNode,
    DocumentVersion,
    Evidence,
    Requirement,
    RequirementEvidence,
    User,
    UserRole,
)
from app.integrations.external_connectors import ConnectorExecutionResult
from app.schemas.advanced import (
    AgentRunCreate,
    ChallengeDraftCreate,
    CompetitiveAnalysisCreate,
    CompetitiveFindingReviewRequest,
    ConnectorUpdate,
    IntegrationRunCreate,
    KnowledgeCreateRequest,
    MarketCheckCreate,
    QuoteScenarioCreate,
    WorkItemCreate,
)
from app.schemas.projects import ProjectCreate, ProjectMemberCreate
from app.services.advanced_service import AdvancedService
from app.services.project_service import ProjectService

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_bytes(self, object_key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[object_key] = content

    def delete_object(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    @contextmanager
    def open_object(self, object_key: str):
        content = self.objects[object_key]

        class Response:
            def stream(self, amt: int):
                for offset in range(0, len(content), amt):
                    yield content[offset : offset + amt]

        yield Response()


class CapturingIntegrationPublisher:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object, dict[str, object]]] = []

    def publish_integration_run(self, run_id, project_id, payload: dict[str, object]) -> str:
        self.calls.append((run_id, project_id, payload))
        return "integration-test-task"


class SuccessfulConnectorExecutor:
    def execute(self, connector_code, operation, integration_run_id, project_id, payload):
        del connector_code, operation, integration_run_id, project_id, payload
        return ConnectorExecutionResult(
            external_reference="external-test-reference",
            summary={"accepted": True, "http_status": 200, "response_kind": "object"},
        )


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_advanced_p1_p2_workflows_are_evidence_bound_and_manual() -> None:
    engine = create_engine(DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    now = datetime.now(UTC)
    admin = User(
        id=uuid4(),
        username=f"advanced-admin-{uuid4().hex[:10]}",
        password_hash=hash_password("advanced-test-password"),
        display_name="高级功能管理员",
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    specialist = User(
        id=uuid4(),
        username=f"advanced-specialist-{uuid4().hex[:10]}",
        password_hash=hash_password("advanced-test-password"),
        display_name="高级功能投标专员",
        status="ACTIVE",
        created_at=now,
        updated_at=now,
    )
    try:
        session.add_all([admin, specialist])
        session.add_all(
            [
                UserRole(user_id=admin.id, role_code=SYSTEM_ADMIN, created_at=now),
                UserRole(user_id=specialist.id, role_code=BID_SPECIALIST, created_at=now),
            ]
        )
        session.commit()
        project_service = ProjectService(session)
        project_response = project_service.create(
            admin.id,
            ProjectCreate(
                name="高级功能测试项目",
                code=f"ADV-{uuid4().hex[:10]}",
                purchaser="测试招标人",
                project_type="服务",
                region="上海",
                bid_deadline=now + timedelta(days=7),
            ),
        )
        project = project_service.get_visible(project_response.id, admin.id, {SYSTEM_ADMIN})
        project_service.add_member(
            project,
            admin.id,
            ProjectMemberCreate(user_id=specialist.id, role_code=BID_SPECIALIST),
            is_admin=True,
        )

        document = Document(
            id=uuid4(),
            project_id=project.id,
            document_type="TENDER",
            logical_name="高级测试招标文件.pdf",
            current_version_id=None,
            created_at=now,
            created_by=admin.id,
            deleted_at=None,
        )
        session.add(document)
        session.flush()
        version = DocumentVersion(
            id=uuid4(),
            document_id=document.id,
            version_no=1,
            file_name="高级测试招标文件.pdf",
            file_size=128,
            mime_type="application/pdf",
            object_key="tests/advanced/source",
            sha256="a" * 64,
            parse_status="READY",
            parse_output_key=None,
            error_code=None,
            error_message=None,
            created_at=now,
            created_by=admin.id,
            completed_at=now,
        )
        session.add(version)
        session.flush()
        document.current_version_id = version.id
        node = DocumentNode(
            id=uuid4(),
            document_version_id=version.id,
            parent_node_id=None,
            node_type="PARAGRAPH",
            page_number=1,
            section_path="测试章节",
            order_no=1,
            content="指定品牌型号，要求本地注册企业提供服务。",
            content_hash="b" * 64,
            bbox=None,
            metadata_={},
            created_at=now,
        )
        session.add(node)
        session.flush()
        evidence = Evidence(
            id=uuid4(),
            source_type="DOCUMENT_TEXT",
            document_version_id=version.id,
            document_node_id=node.id,
            page_number=1,
            quoted_text="指定品牌型号，要求本地注册企业提供服务。",
            content_hash="b" * 64,
            bbox=None,
            source_reference={},
            confidence=1.0,
            created_at=now,
            created_by=admin.id,
        )
        requirement = Requirement(
            id=uuid4(),
            project_id=project.id,
            category="QUALIFICATION",
            title="本地注册资质要求",
            description=None,
            conditions={},
            is_mandatory=True,
            score=None,
            confidence=1,
            review_status="CONFIRMED",
            primary_evidence_id=evidence.id,
            reviewed_at=now,
            reviewed_by=admin.id,
            review_note="测试确认",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add_all([evidence, requirement])
        session.flush()
        session.add(
            RequirementEvidence(
                requirement_id=requirement.id,
                evidence_id=evidence.id,
                relation="SOURCE",
                created_at=now,
            )
        )
        session.commit()

        storage = MemoryObjectStorage()
        service = AdvancedService(session, storage, Settings())
        admin_roles = {SYSTEM_ADMIN}
        knowledge = service.create_knowledge(
            admin.id,
            admin_roles,
            KnowledgeCreateRequest(
                knowledge_type="LEGAL",
                title="测试法规",
                authority="测试机关",
                source_reference="TEST-LEGAL-001",
                content="仅用于测试的人工录入法规内容。",
            ),
        )
        published = service.publish_knowledge(knowledge.version_id, admin.id, admin_roles)
        analysis = service.create_competitive_analysis(
            project.id,
            admin.id,
            admin_roles,
            CompetitiveAnalysisCreate(
                evidence_ids=[evidence.id],
                requirement_id=requirement.id,
                knowledge_version_ids=[published.version_id],
            ),
        )
        assert analysis.status == "READY"
        assert {finding.category for finding in analysis.findings} >= {
            "BRAND_OR_PARAMETER",
            "GEOGRAPHIC_RESTRICTION",
        }
        reviewed = service.review_competitive_finding(
            project.id,
            analysis.findings[0].id,
            admin.id,
            admin_roles,
            CompetitiveFindingReviewRequest(
                status="CONFIRMED",
                resolution="建议人工法务核查。",
                knowledge_version_ids=[published.version_id],
            ),
        )
        assert reviewed.status == "CONFIRMED"

        draft = service.create_challenge_draft(
            project.id,
            admin.id,
            admin_roles,
            ChallengeDraftCreate(
                title="测试质疑函",
                subject="特定参数与地域限制",
                fact_statement="基于已引用 Evidence 的人工事实说明。",
                requested_action="请人工复核相关条款。",
                evidence_ids=[evidence.id],
            ),
        )
        assert draft.has_docx and draft.has_pdf
        assert len(storage.objects) == 2

        quote = service.create_quote_scenario(
            project.id,
            admin.id,
            admin_roles,
            QuoteScenarioCreate(
                name="基准报价",
                cost_excluding_tax="100",
                tax_rate="0.06",
                target_margin_rate="0.10",
                risk_adjustment="5",
            ),
        )
        assert quote.calculations["quote_excluding_tax"] == "116.67"
        locked = service.lock_quote_scenario(project.id, quote.id, admin.id, admin_roles)
        assert locked.status == "LOCKED"
        copied = service.copy_quote_scenario(project.id, quote.id, admin.id, admin_roles)
        assert copied.version_no == 2

        work_item = service.create_work_item(
            project.id,
            admin.id,
            admin_roles,
            WorkItemCreate(
                title="复核高级风险",
                assignee_id=specialist.id,
                target_type="EVIDENCE",
                target_id=evidence.id,
            ),
        )
        assert work_item.status == "OPEN"
        notifications = service.list_notifications(specialist.id)
        assert notifications[0].notification_type == "WORK_ITEM_ASSIGNED"

        market = service.create_market_check(
            project.id,
            admin.id,
            admin_roles,
            MarketCheckCreate(
                evidence_id=evidence.id,
                parameter="指定品牌参数",
                source_name="人工市场资料",
                source_reference="MANUAL-001",
                excerpt="人工摘录，不涉及自动抓取。",
                conclusion="INCONCLUSIVE",
            ),
        )
        assert market.conclusion == "INCONCLUSIVE"
        assert service.rebuild_graph(project.id, admin.id, admin_roles).nodes
        agent = service.create_agent_run(
            project.id,
            admin.id,
            admin_roles,
            AgentRunCreate(workflow="COMPLIANCE_REVIEW", goal="形成待人工采纳的复核清单"),
        )
        assert agent.status == "SUCCEEDED"
        assert agent.result["requires_manual_adoption"] is True
        integration = service.create_integration_run(
            project.id,
            admin.id,
            admin_roles,
            IntegrationRunCreate(
                connector_code="ERP", operation="LOOKUP", payload={"reference": "x"}
            ),
        )
        assert integration.status == "FAILED"
        assert integration.error_code == "INTEGRATION_UNAVAILABLE"

        publisher = CapturingIntegrationPublisher()
        connected_service = AdvancedService(
            session,
            storage,
            Settings(erp_integration_base_url="http://connector.example.invalid"),
            publisher,
        )
        connected_service.update_connector(
            "ERP", admin.id, admin_roles, ConnectorUpdate(is_enabled=True)
        )
        queued = connected_service.create_integration_run(
            project.id,
            admin.id,
            admin_roles,
            IntegrationRunCreate(
                connector_code="ERP", operation="LOOKUP", payload={"reference": "y"}
            ),
        )
        assert queued.status == "QUEUED"
        assert len(publisher.calls) == 1
        connected_service.execute_integration_run(
            queued.id,
            project.id,
            {"reference": "y"},
            SuccessfulConnectorExecutor(),
        )
        completed = next(
            item
            for item in connected_service.list_integration_runs(project.id, admin.id, admin_roles)
            if item.id == queued.id
        )
        assert completed.status == "SUCCEEDED"
        assert completed.external_reference == "external-test-reference"
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
