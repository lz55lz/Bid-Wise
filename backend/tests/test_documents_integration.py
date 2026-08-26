import io
import os
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import UploadFile
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.datastructures import Headers

from app.core.config import Settings
from app.core.constants import SYSTEM_ADMIN
from app.core.errors import DomainError
from app.core.security import hash_password
from app.db.models import (
    AiRun,
    AiRunEvidence,
    DocumentNode,
    DocumentVersion,
    EnterpriseMaterial,
    Evidence,
    MatchResult,
    ProjectField,
    Requirement,
    RequirementEvidence,
    Risk,
    RiskEvidence,
    RiskReview,
    Rule,
    RuleVersion,
    SearchChunk,
    Task,
    TenderProject,
    User,
    UserRole,
)
from app.integrations.ai.reranker import RankerUnavailable
from app.integrations.mineru import MinerUParseResult, ParsedNode
from app.integrations.vector_store import VectorSearchHit
from app.schemas.auth import UserCreate, UserRoleUpdate
from app.schemas.extraction import RequirementExtractionResult
from app.schemas.matches import MatchOverrideRequest
from app.schemas.materials import EnterpriseMaterialCreate, EnterpriseMaterialUpdate
from app.schemas.projects import ProjectCreate
from app.schemas.rag import RagAnswerDraft
from app.schemas.risks import RiskReviewRequest
from app.schemas.rules import RuleCreateRequest, RuleVersionRequest
from app.services.auth_service import AuthService
from app.services.decision_service import DecisionService
from app.services.document_cleaning_service import DocumentCleaningService
from app.services.document_indexing_service import DocumentIndexingService
from app.services.document_parsing_service import DocumentParsingService
from app.services.document_service import DocumentService
from app.services.matching_service import MatchingService
from app.services.material_service import MaterialService
from app.services.project_service import ProjectService
from app.services.rag_service import RagService
from app.services.report_service import ReportService
from app.services.requirement_extraction_service import RequirementExtractionService
from app.services.risk_service import RiskService
from app.services.rule_service import RuleService

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.integration


class CleanScanner:
    def scan(self, path: Path) -> None:
        assert path.exists()


class MemoryObjectStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_file(self, object_key: str, path: Path, content_type: str) -> None:
        del content_type
        self.objects[object_key] = path.read_bytes()

    def put_bytes(self, object_key: str, content: bytes, content_type: str) -> None:
        del content_type
        self.objects[object_key] = content

    def delete_object(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def download_to_path(self, object_key: str, destination: Path) -> None:
        destination.write_bytes(self.objects[object_key])

    @contextmanager
    def open_object(self, object_key: str):
        content = self.objects[object_key]

        class Response:
            def stream(self, amt: int):
                for offset in range(0, len(content), amt):
                    yield content[offset : offset + amt]

        yield Response()


class FakePublisher:
    def publish_parse_document(self, task_id, document_version_id) -> str:
        return f"celery-{task_id}-{document_version_id}"

    def publish_clean_document(self, task_id, document_version_id) -> str:
        return f"celery-clean-{task_id}-{document_version_id}"

    def publish_index_document(self, task_id, document_version_id) -> str:
        return f"celery-index-{task_id}-{document_version_id}"

    def publish_extract_requirements(self, task_id, document_version_id) -> str:
        return f"celery-extract-{task_id}-{document_version_id}"


class FakeRiskPublisher:
    def publish_run_risk_check(self, task_id, project_id) -> str:
        return f"celery-risk-{task_id}-{project_id}"


class FakeReportPublisher:
    def publish_generate_report(self, task_id, report_id) -> str:
        return f"celery-report-{task_id}-{report_id}"


class FakeMinerU:
    def parse(self, source_path: Path, source_mime_type: str) -> MinerUParseResult:
        assert source_path.read_bytes().startswith(b"%PDF-")
        assert source_mime_type == "application/pdf"
        return MinerUParseResult(
            nodes=(
                ParsedNode("SECTION", "第一章 项目概况", page_number=1, section_path="第一章"),
                ParsedNode("PARAGRAPH", "项目投标截止时间。", page_number=1, section_path="第一章"),
            ),
            raw_output=b'{"format":"mineru-test"}',
        )


class FakeEmbeddingClient:
    def embed(self, contents: list[str]) -> list[list[float]]:
        return [[float(index), 0.5, 0.25] for index, _ in enumerate(contents, start=1)]


class FakeVectorStore:
    def __init__(self) -> None:
        self.records = []

    def upsert(self, records) -> None:
        self.records.extend(records)

    def search(self, vector, project_id, limit) -> list[VectorSearchHit]:
        assert vector and limit > 0
        return [
            VectorSearchHit(record.pk) for record in self.records if record.project_id == project_id
        ][:limit]

    def search_enterprise(self, vector, limit) -> list[VectorSearchHit]:
        assert vector and limit > 0
        return [
            VectorSearchHit(record.pk)
            for record in self.records
            if record.project_id == "" and record.chunk_type == "ENTERPRISE"
        ][:limit]


class FakeRequirementLlm:
    def extract_requirements(self, nodes):
        return RequirementExtractionResult.model_validate(
            {
                "project_fields": [
                    {
                        "field_code": "PURCHASER",
                        "value_json": {"name": "Purchaser"},
                        "evidence_node_ids": [nodes[0]["node_id"]],
                    }
                ],
                "requirements": [
                    {
                        "category": "QUALIFICATION",
                        "title": "具备相关资质",
                        "is_mandatory": True,
                        "evidence_node_ids": [nodes[0]["node_id"]],
                    }
                ]
            }
        )


class FakeReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        assert query
        return [float(index) for index, _ in enumerate(documents, start=1)]


class FailingReranker:
    def rerank(self, query: str, documents: list[str]) -> list[float]:
        del query, documents
        raise RankerUnavailable("test ranker failure")


class FakeRagLlm:
    def answer_question(self, question: str, contexts: list[dict[str, str]]) -> RagAnswerDraft:
        assert question and contexts
        return RagAnswerDraft(
            answer="基于已授权的招标文件证据生成的回答。",
            evidence_ids=[contexts[0]["evidence_id"]],
        )


class NeverCalledRagLlm:
    def answer_question(self, question: str, contexts: list[dict[str, str]]) -> RagAnswerDraft:
        del question, contexts
        raise AssertionError("reranker failure must not bypass reranking")


def _configured_ai_settings() -> Settings:
    return Settings(
        llm_base_url="http://llm.invalid",
        llm_api_key="test-key",
        reranker_base_url="http://reranker.invalid",
        reranker_api_key="test-key",
        embedding_base_url="http://embedding.invalid",
        embedding_api_key="test-key",
    )


def _upload(name: str = "tender.pdf") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"%PDF-1.7\nminimal tender content"),
        filename=name,
        headers=Headers({"content-type": "application/pdf"}),
    )


@pytest.mark.skipif(not DATABASE_URL, reason="TEST_DATABASE_URL is not configured")
def test_document_versions_tasks_and_normalized_nodes_use_real_postgres() -> None:
    engine = create_engine(DATABASE_URL)
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    storage = MemoryObjectStorage()
    now = datetime.now(UTC)
    admin = User(
        username=f"document-admin-{uuid4().hex[:12]}",
        password_hash=hash_password("integration-test-password"),
        display_name="Document test administrator",
        created_at=now,
        updated_at=now,
    )
    try:
        session.add(admin)
        session.flush()
        session.add(UserRole(user_id=admin.id, role_code=SYSTEM_ADMIN, created_at=now))
        session.commit()
        auth_service = AuthService(session, Settings(jwt_secret_key="integration-test-secret"))
        managed_user = auth_service.create_user(
            admin.id,
            UserCreate(
                username=f"reader-{uuid4().hex[:12]}",
                password="integration-test-password",
                display_name="Read only user",
                roles={"READ_ONLY"},
            ),
        )
        updated_user = auth_service.update_user_roles(
            admin.id,
            managed_user.id,
            UserRoleUpdate(roles={"BID_SPECIALIST"}, status="DISABLED"),
        )
        assert updated_user.status == "DISABLED"
        assert updated_user.roles == ["BID_SPECIALIST"]
        assert any(user.id == updated_user.id for user in auth_service.list_users())
        assert {role.code for role in auth_service.list_roles()} >= {"SYSTEM_ADMIN", "READ_ONLY"}
        project = ProjectService(session).create(
            admin.id,
            ProjectCreate(
                name="Document integration project",
                code=f"DOC-{uuid4().hex[:12]}",
                purchaser="Purchaser",
                project_type="Service",
                region="Shanghai",
                bid_deadline=now + timedelta(days=7),
            ),
        )
        service = DocumentService(session, storage, FakePublisher())

        first = service.upload_tender_document(
            project.id, admin.id, {SYSTEM_ADMIN}, "TENDER", _upload(), 1024 * 1024
        )
        second = service.upload_tender_document(
            project.id, admin.id, {SYSTEM_ADMIN}, "TENDER", _upload(), 1024 * 1024
        )

        assert first.document_id == second.document_id
        assert first.version_no == 1
        assert second.version_no == 2
        assert first.task.status == "QUEUED"
        assert len(storage.objects) == 2

        DocumentParsingService(session, storage, FakeMinerU()).process(
            second.task.id, second.document_version_id
        )
        version = session.get(DocumentVersion, second.document_version_id)
        task = session.get(Task, second.task.id)
        nodes = list(
            session.scalars(
                select(DocumentNode)
                .where(DocumentNode.document_version_id == second.document_version_id)
                .order_by(DocumentNode.order_no)
            )
        )
        evidences = list(
            session.scalars(
                select(Evidence).where(Evidence.document_version_id == second.document_version_id)
            )
        )
        assert version is not None and version.parse_status == "STRUCTURING"
        assert task is not None and task.status == "SUCCEEDED"
        assert [node.node_type for node in nodes] == ["SECTION", "PARAGRAPH"]
        assert nodes[0].content_hash
        assert [evidence.source_type for evidence in evidences] == [
            "DOCUMENT_SECTION",
            "DOCUMENT_TEXT",
        ]
        assert all(evidence.document_node_id for evidence in evidences)

        clean_task = session.scalar(
            select(Task).where(
                Task.task_type == "CLEAN_DOCUMENT",
                Task.target_id == second.document_version_id,
            )
        )
        assert clean_task is not None and clean_task.status == "QUEUED"
        DocumentCleaningService(session).process(clean_task.id, second.document_version_id)
        version = session.get(DocumentVersion, second.document_version_id)
        assert version is not None and version.parse_status == "STRUCTURING"
        assert version.cleaning_summary is not None
        assert nodes[0].content and nodes[0].cleaned_content

        index_task = session.scalar(
            select(Task).where(
                Task.task_type == "INDEX_DOCUMENT",
                Task.target_id == second.document_version_id,
            )
        )
        assert index_task is not None and index_task.status == "QUEUED"
        vector_store = FakeVectorStore()
        DocumentIndexingService(session, FakeEmbeddingClient(), vector_store).process(
            index_task.id, second.document_version_id
        )
        index_task = session.get(Task, index_task.id)
        version = session.get(DocumentVersion, second.document_version_id)
        chunks = list(
            session.scalars(
                select(SearchChunk).where(
                    SearchChunk.source_document_version_id == second.document_version_id
                )
            )
        )
        ai_runs = list(session.scalars(select(AiRun).where(AiRun.task_id == index_task.id)))
        assert index_task is not None and index_task.status == "SUCCEEDED"
        assert version is not None and version.parse_status == "STRUCTURING"
        indexable_nodes = [node for node in nodes if node.cleaning_metadata.get("indexable")]
        assert len(chunks) >= len(indexable_nodes)
        assert len(chunks) == len(vector_store.records)
        assert all(chunk.evidence_id for chunk in chunks)
        assert all(chunk.indexed_at for chunk in chunks)
        assert len(ai_runs) == 1 and ai_runs[0].status == "SUCCEEDED"

        extraction_task = session.scalar(
            select(Task).where(
                Task.task_type == "EXTRACT_REQUIREMENTS",
                Task.target_id == second.document_version_id,
            )
        )
        assert extraction_task is not None and extraction_task.status == "QUEUED"
        RequirementExtractionService(session, FakeRequirementLlm()).process(
            extraction_task.id, second.document_version_id
        )
        version = session.get(DocumentVersion, second.document_version_id)
        extraction_task = session.get(Task, extraction_task.id)
        assert extraction_task is not None and extraction_task.status == "SUCCEEDED"
        assert version is not None and version.parse_status == "READY"
        extraction_run = session.scalar(select(AiRun).where(AiRun.task_id == extraction_task.id))
        assert extraction_run is not None
        assert session.scalar(
            select(AiRunEvidence).where(AiRunEvidence.ai_run_id == extraction_run.id)
        )
        field = session.scalar(
            select(ProjectField).where(
                ProjectField.project_id == project.id, ProjectField.field_code == "PURCHASER"
            )
        )
        assert field is not None and field.primary_evidence_id is not None

        failed_index = Task(
            id=uuid4(),
            task_type="INDEX_DOCUMENT",
            target_type="DOCUMENT_VERSION",
            target_id=second.document_version_id,
            idempotency_key=f"index:{second.document_version_id}",
            status="FAILED",
            attempt=2,
            error_code="VECTOR_STORE_UNAVAILABLE",
            error_message="test failure",
            created_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            created_by=admin.id,
        )
        session.add(failed_index)
        version.parse_status = "FAILED"
        version.error_code = failed_index.error_code
        session.commit()
        retry = service.retry_document(second.document_id, admin.id, {SYSTEM_ADMIN})
        assert retry.task.task_type == "INDEX_DOCUMENT"
        retried_index = session.get(Task, retry.task.id)
        assert retried_index is not None
        assert retried_index.parent_task_id == failed_index.id
        assert retried_index.celery_task_id is not None

        rag_answer = RagService(
            session,
            _configured_ai_settings(),
            FakeEmbeddingClient(),
            vector_store,
            FakeReranker(),
            FakeRagLlm(),
        ).answer(project.id, admin.id, {SYSTEM_ADMIN}, "项目要求是什么？")
        assert not rag_answer.no_evidence
        assert rag_answer.citations
        assert rag_answer.citations[0].document_version_id == second.document_version_id
        assert rag_answer.citations[0].quoted_text

        with pytest.raises(DomainError) as rerank_failure:
            RagService(
                session,
                _configured_ai_settings(),
                FakeEmbeddingClient(),
                vector_store,
                FailingReranker(),
                NeverCalledRagLlm(),
            ).answer(project.id, admin.id, {SYSTEM_ADMIN}, "请重新说明项目要求")
        assert rerank_failure.value.code == "AI_SERVICE_UNAVAILABLE"

        material_service = MaterialService(session)
        material = material_service.create(
            admin.id,
            {SYSTEM_ADMIN},
            EnterpriseMaterialCreate(
                material_type="CERTIFICATE",
                name="测试资质证书",
                material_no="CERT-001",
                valid_to=(now + timedelta(days=365)).date(),
            ),
        )
        proof = service.upload_enterprise_material_document(
            admin.id, {SYSTEM_ADMIN}, _upload("proof.pdf"), 1024 * 1024
        )
        material_service.attach_document(
            material.id,
            proof.document_id,
            proof.document_version_id,
            admin.id,
            {SYSTEM_ADMIN},
        )
        DocumentParsingService(session, storage, FakeMinerU()).process(
            proof.task.id, proof.document_version_id
        )
        proof_clean_task = session.scalar(
            select(Task).where(
                Task.target_id == proof.document_version_id,
                Task.task_type == "CLEAN_DOCUMENT",
            )
        )
        assert proof_clean_task is not None
        DocumentCleaningService(session).process(proof_clean_task.id, proof.document_version_id)
        proof_version = session.get(DocumentVersion, proof.document_version_id)
        assert proof_version is not None and proof_version.parse_status == "STRUCTURING"
        proof_index_task = session.scalar(
            select(Task).where(
                Task.target_id == proof.document_version_id,
                Task.task_type == "INDEX_DOCUMENT",
            )
        )
        assert proof_index_task is not None
        DocumentIndexingService(session, FakeEmbeddingClient(), vector_store).process(
            proof_index_task.id, proof.document_version_id
        )
        proof_version = session.get(DocumentVersion, proof.document_version_id)
        assert proof_version is not None and proof_version.parse_status == "READY"
        confirmed_material = material_service.update(
            material.id,
            admin.id,
            {SYSTEM_ADMIN},
            EnterpriseMaterialUpdate(status="CONFIRMED"),
        )
        assert confirmed_material.status == "CONFIRMED"
        assert confirmed_material.evidence_ids

        requirement = session.scalar(
            select(Requirement).where(Requirement.project_id == project.id)
        )
        assert requirement is not None
        requirement.review_status = "CONFIRMED"
        requirement.conditions = {
            "all": [{"dimension": "evidence", "operator": "REQUIRED", "value": True}]
        }
        session.commit()
        matching = MatchingService(session)
        matches = matching.run(project.id, admin.id, {SYSTEM_ADMIN})
        matched = next(item for item in matches if item.material_id == material.id)
        assert matched.automatic_status == "MATCHED"
        assert matched.final_status == "MATCHED"
        assert matched.evidence_ids
        overridden = matching.override(
            matched.id,
            admin.id,
            {SYSTEM_ADMIN},
            MatchOverrideRequest(final_status="PARTIAL", reason="补充核验后保留人工判断"),
        )
        assert overridden.is_overridden and overridden.final_status == "PARTIAL"
        rerun = next(
            item
            for item in matching.run(project.id, admin.id, {SYSTEM_ADMIN})
            if item.id == matched.id
        )
        assert rerun.automatic_status == "MATCHED"
        assert rerun.final_status == "PARTIAL"

        material_record = session.get(EnterpriseMaterial, material.id)
        assert material_record is not None
        material_record.status = "PENDING"
        session.commit()
        missing_matches = matching.run(project.id, admin.id, {SYSTEM_ADMIN})
        assert all(item.final_status == "MISSING" for item in missing_matches)
        persisted_match = session.get(MatchResult, matched.id)
        assert persisted_match is not None and not persisted_match.is_current
        material_record.status = "CONFIRMED"
        session.commit()
        matching.run(project.id, admin.id, {SYSTEM_ADMIN})

        enterprise_rag = RagService(
            session,
            _configured_ai_settings(),
            FakeEmbeddingClient(),
            vector_store,
            FakeReranker(),
            FakeRagLlm(),
        ).answer(project.id, admin.id, {SYSTEM_ADMIN}, "企业资质证明是什么？")
        assert not enterprise_rag.no_evidence
        assert any(
            citation.document_version_id == proof.document_version_id
            for citation in enterprise_rag.citations
        )

        assert requirement.primary_evidence_id is not None
        mandatory_without_material = Requirement(
            id=uuid4(),
            project_id=project.id,
            category="BUSINESS",
            title="必须提供业绩证明",
            description="未提供即无效",
            conditions={"all": []},
            is_mandatory=True,
            score=None,
            confidence=None,
            review_status="CONFIRMED",
            primary_evidence_id=requirement.primary_evidence_id,
            reviewed_at=now,
            reviewed_by=admin.id,
            review_note="测试用已确认要求",
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(mandatory_without_material)
        session.add(
            RequirementEvidence(
                requirement_id=mandatory_without_material.id,
                evidence_id=requirement.primary_evidence_id,
                relation="SOURCE",
                created_at=now,
            )
        )
        requirement.conditions = {
            "all": [{"dimension": "amount", "operator": "GTE", "value": 1000000}]
        }
        material_record = session.get(EnterpriseMaterial, material.id)
        assert material_record is not None
        material_record.valid_to = (now - timedelta(days=1)).date()
        project_record = session.get(TenderProject, project.id)
        assert project_record is not None
        project_record.bid_deadline = now - timedelta(hours=1)
        project_record.updated_at = now
        session.commit()

        risk_service = RiskService(session)
        risks = risk_service.run(project.id, admin.id, {SYSTEM_ADMIN})
        risk_codes = set(
            session.scalars(
                select(Rule.code)
                .join(RuleVersion, RuleVersion.rule_id == Rule.id)
                .join(Risk, Risk.rule_version_id == RuleVersion.id)
                .where(Risk.project_id == project.id)
            )
        )
        assert risk_codes == {
            "DEADLINE_EXPIRED",
            "CERTIFICATE_EXPIRED",
            "QUANTITATIVE_REQUIREMENT_UNMET",
            "MANDATORY_EVIDENCE_MISSING",
        }
        assert all(item.evidence_ids and item.primary_evidence_id for item in risks)
        assert {(item.risk_type, item.severity) for item in risks} >= {
            ("TIME", "CRITICAL"),
            ("QUALIFICATION", "HIGH"),
            ("DOCUMENT", "HIGH"),
        }
        risk_count = len(risks)
        evidence_link_count = len(
            list(
                session.scalars(
                    select(RiskEvidence)
                    .join(Risk, Risk.id == RiskEvidence.risk_id)
                    .where(Risk.project_id == project.id)
                )
            )
        )
        assert len(risk_service.run(project.id, admin.id, {SYSTEM_ADMIN})) == risk_count
        assert (
            len(
                list(
                    session.scalars(
                        select(RiskEvidence)
                        .join(Risk, Risk.id == RiskEvidence.risk_id)
                        .where(Risk.project_id == project.id)
                    )
                )
            )
            == evidence_link_count
        )

        reviewed = risk_service.review(
            project.id,
            next(item.id for item in risks if item.risk_type == "TIME"),
            admin.id,
            {SYSTEM_ADMIN},
            RiskReviewRequest(status="CONFIRMED", resolution="建议核查延期或终止投标准备。"),
        )
        assert reviewed.status == "CONFIRMED"
        assert (
            session.scalar(select(RiskReview).where(RiskReview.risk_id == reviewed.id)) is not None
        )
        assert len(reviewed.evidence_ids) >= 2

        custom_rule = RuleService(session).create(
            admin.id,
            {SYSTEM_ADMIN},
            RuleCreateRequest(
                code="PROJECT_IS_DRAFT",
                name="项目仍处于草稿状态",
                risk_type="DOCUMENT",
                severity="MEDIUM",
                definition={
                    "all": [{"source": "project", "field": "status", "op": "EQ", "value": "DRAFT"}],
                    "message_template": "建议核查项目状态后再继续投标准备。",
                    "evidence_selector": {"field_code": "status"},
                },
            ),
        )
        custom_risks = risk_service.run(project.id, admin.id, {SYSTEM_ADMIN})
        assert any(
            item.trigger_data.get("rule_code") == "PROJECT_IS_DRAFT" for item in custom_risks
        )
        disabled_rule = RuleService(session).version(
            custom_rule.id,
            admin.id,
            {SYSTEM_ADMIN},
            RuleVersionRequest(
                severity="MEDIUM",
                definition={
                    "all": [{"source": "project", "field": "status", "op": "EQ", "value": "DRAFT"}],
                    "message_template": "建议核查项目状态后再继续投标准备。",
                    "evidence_selector": {"field_code": "status"},
                },
                is_enabled=False,
            ),
        )
        assert disabled_rule.active_version is None
        queued_risk_task = risk_service.submit(
            project.id, admin.id, {SYSTEM_ADMIN}, FakeRiskPublisher()
        )
        duplicate_risk_task = risk_service.submit(
            project.id, admin.id, {SYSTEM_ADMIN}, FakeRiskPublisher()
        )
        assert queued_risk_task.task_type == "RUN_RISK_CHECK"
        assert queued_risk_task.status == "QUEUED"
        assert duplicate_risk_task.id == queued_risk_task.id

        decision_service = DecisionService(session)
        decision = decision_service.generate(project.id, admin.id, {SYSTEM_ADMIN})
        assert decision.suggestion == "REJECT"
        assert decision.hard_constraint_result["deadline_expired"] is True
        assert decision.evidence_ids

        report_service = ReportService(session, storage)
        report_task = report_service.submit(
            project.id, admin.id, {SYSTEM_ADMIN}, FakeReportPublisher()
        )
        assert report_task.task_type == "GENERATE_REPORT"
        assert report_task.target_type == "REPORT"
        generated_report = report_service.generate(report_task.target_id, admin.id, {SYSTEM_ADMIN})
        assert generated_report.status == "READY"
        assert len(generated_report.sections) == 8
        assert all(section.evidence_ids for section in generated_report.sections[1:])
        assert any(key.endswith("report.docx") for key in storage.objects)
        assert any(key.endswith("report.pdf") for key in storage.objects)

        with pytest.raises(DomainError) as invalid_upload:
            service.upload_tender_document(
                project.id,
                admin.id,
                {SYSTEM_ADMIN},
                "TENDER",
                UploadFile(
                    file=io.BytesIO(b"not a pdf"),
                    filename="unsafe.pdf",
                    headers=Headers({"content-type": "application/pdf"}),
                ),
                1024 * 1024,
            )
        assert invalid_upload.value.code == "FILE_SECURITY_REJECTED"
    finally:
        session.close()
        transaction.rollback()
        connection.close()
        engine.dispose()
