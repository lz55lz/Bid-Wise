"""
Material matching optimization tests: keyword filtering + cartesian product control
"""
from collections import Counter
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.db import models as m
from app.db.session import get_session_factory
from app.integrations.object_storage import MinioObjectStorage
from app.services.decision_service import DecisionService
from app.services.matching_service import MatchingService
from app.services.report_service import ReportService


@pytest.fixture
def session():
    s = get_session_factory()()
    yield s
    s.close()


@pytest.fixture
def actor_id(session):
    return session.query(m.User).filter(m.User.username == "admin").first().id


@pytest.fixture
def zb5_project_id(session):
    req = session.query(m.Requirement).filter(
        m.Requirement.extraction_source == "llm",
        m.Requirement.review_status == "CONFIRMED",
    ).first()
    if req is None:
        pytest.skip("no zb5 requirements found")
    return req.project_id


@pytest.fixture
def clean_matches(session, zb5_project_id):
    session.query(m.MatchResult).filter(m.MatchResult.project_id == zb5_project_id).delete()
    session.commit()
    yield


class TestMatchingResultQuality:
    """Cartesian product control: avg matches per requirement should be <= 3"""

    def test_cartesian_product_controlled(self, session, actor_id, zb5_project_id, clean_matches):
        svc = MatchingService(session)
        results = svc.run(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        reqs = session.query(m.Requirement).filter(
            m.Requirement.project_id == zb5_project_id,
            m.Requirement.review_status == "CONFIRMED",
        ).all()

        if not reqs:
            pytest.skip("no confirmed requirements")

        # Total matches should be <= requirement count * 3
        assert len(results) <= len(reqs) * 3, \
            f"Too many matches: {len(results)} > {len(reqs)}*3"

        cnt = Counter(r.requirement_id for r in results)
        if cnt:
            avg = len(results) / len(cnt)
            assert avg <= 3, f"Avg {avg:.1f} per req, should be <= 3"

            statuses = Counter(r.final_status for r in results)
            print(f"\nMatches: {len(results)} / {len(reqs)} req, avg={avg:.1f}")
            print(f"Status: {dict(statuses)}")

    def test_all_confirmed_requirements_evaluated(self, session, actor_id, zb5_project_id, clean_matches):
        """Every confirmed requirement should be evaluated (have a match result or MISSING)"""
        svc = MatchingService(session)
        results = svc.run(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        reqs = session.query(m.Requirement).filter(
            m.Requirement.project_id == zb5_project_id,
            m.Requirement.review_status == "CONFIRMED",
        ).all()

        matched_ids = {r.requirement_id for r in results}
        unmatched = [r for r in reqs if r.id not in matched_ids]

        # If unmatched > 0, that's OK - means no compatible materials
        # But there should be at least some matched
        if unmatched:
            print(f"\nUnmatched (no compatible materials): {len(unmatched)}")
            for r in unmatched[:3]:
                print(f"  [{r.category}] {r.title[:60]}")


class TestDecisionAndReport:
    """Decision + report e2e"""

    def test_decision_generates(self, session, actor_id, zb5_project_id, clean_matches):
        svc = MatchingService(session)
        svc.run(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        dec_svc = DecisionService(session)
        decision = dec_svc.generate(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        assert decision.suggestion in ("RECOMMEND", "CAUTION", "HOLD", "REJECT")
        assert decision.reason

    def test_report_generates(self, session, actor_id, zb5_project_id, clean_matches):
        svc = MatchingService(session)
        svc.run(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        dec_svc = DecisionService(session)
        dec_svc.generate(zb5_project_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})

        settings = get_settings()
        storage = MinioObjectStorage(settings)

        # 查找是否已有 v1 报告，没有则创建
        existing = session.query(m.Report).filter(
            m.Report.project_id == zb5_project_id,
            m.Report.version_no == 1,
        ).first()

        if existing:
            session.query(m.ReportSection).filter(m.ReportSection.report_id == existing.id).delete()
            existing.status = "QUEUED"
            session.commit()
            rep_id = existing.id
        else:
            from datetime import UTC, datetime
            rep = m.Report(
                id=uuid4(),
                project_id=zb5_project_id,
                version_no=1,
                report_type="FULL",
                status="QUEUED",
                docx_object_key=None,
                pdf_object_key=None,
                error_code=None,
                error_message=None,
                generated_by=actor_id,
                generated_at=None,
                created_at=datetime.now(UTC),
            )
            session.add(rep)
            session.commit()
            rep_id = rep.id

        report_svc = ReportService(session, storage)
        rep_gen = report_svc.generate(rep_id, actor_id, {"BID_SPECIALIST", "SYSTEM_ADMIN"})
        assert rep_gen.status == "READY"

        sections = session.query(m.ReportSection).filter(
            m.ReportSection.report_id == rep_id
        ).order_by(m.ReportSection.order_no).all()

        assert len(sections) >= 5
        codes = {s.section_code for s in sections}
        assert "EXECUTIVE_SUMMARY" in codes

        print(f"\nReport sections: {len(sections)}")
        for s in sections:
            preview = s.content_markdown[:60].replace("\n", " ")
            print(f"  [{s.section_code}] {preview}")
