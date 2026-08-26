r"""重置一个测试项目的派生分析数据，保留可复用的源材料。

默认只输出影响范围；只有显式传入 ``--apply`` 才会写数据库。
源项目、原始文档、MinerU 解析节点、Evidence、企业材料和规则配置均不会删除，
因此同一份样本可以快速从“需求抽取”阶段开始反复回归。

用法（从仓库根目录执行）：
    $env:PYTHONPATH = 'backend'
    .\.venv\Scripts\python.exe backend/scripts/reset_test_project.py \
        --document-version-id <UUID>
    .\.venv\Scripts\python.exe backend/scripts/reset_test_project.py \
        --project-id <UUID> --apply

需要清空整个测试环境（包括源文档和 MinIO 对象）时，仍使用
``clear_test_data.py --yes``，不要误将该脚本用于该场景。
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import psycopg

from app.core.config import get_settings


@dataclass(frozen=True)
class ResetStep:
    """One fixed, parameterised statement in the project-output reset transaction."""

    name: str
    statement: str


_ACTIVE_ANALYSIS_STATUSES = ("QUEUED", "RUNNING", "WAITING_HUMAN")

# Statements are deliberately fixed rather than dynamically interpolating table names.  It
# keeps the script safe to run from a shell while also making the foreign-key deletion order
# explicit and auditable.  Every statement takes exactly one project UUID parameter.
_RESET_STEPS: tuple[ResetStep, ...] = (
    ResetStep(
        "agent_recommendation_evidences",
        """
        delete from app.agent_recommendation_evidences are
        using app.agent_recommendations ar
        where are.recommendation_id = ar.id and ar.project_id = %s
        """,
    ),
    ResetStep(
        "agent_recommendations",
        "delete from app.agent_recommendations where project_id = %s",
    ),
    ResetStep(
        "agent_run_evidences",
        """
        delete from app.agent_run_evidences are
        using app.agent_runs ar
        where are.agent_run_id = ar.id and ar.project_id = %s
        """,
    ),
    ResetStep(
        "agent_run_steps",
        """
        delete from app.agent_run_steps ars
        using app.agent_runs ar
        where ars.agent_run_id = ar.id and ar.project_id = %s
        """,
    ),
    ResetStep("agent_runs", "delete from app.agent_runs where project_id = %s"),
    ResetStep(
        "competitive_finding_knowledge",
        """
        delete from app.competitive_finding_knowledge cfk
        using app.competitive_findings cf
        join app.competitive_analyses ca on ca.id = cf.analysis_id
        where cfk.finding_id = cf.id and ca.project_id = %s
        """,
    ),
    ResetStep(
        "competitive_finding_evidences",
        """
        delete from app.competitive_finding_evidences cfe
        using app.competitive_findings cf
        join app.competitive_analyses ca on ca.id = cf.analysis_id
        where cfe.finding_id = cf.id and ca.project_id = %s
        """,
    ),
    ResetStep(
        "competitive_findings",
        """
        delete from app.competitive_findings cf
        using app.competitive_analyses ca
        where cf.analysis_id = ca.id and ca.project_id = %s
        """,
    ),
    ResetStep(
        "competitive_analysis_evidences",
        """
        delete from app.competitive_analysis_evidences cae
        using app.competitive_analyses ca
        where cae.analysis_id = ca.id and ca.project_id = %s
        """,
    ),
    ResetStep(
        "competitive_analyses",
        "delete from app.competitive_analyses where project_id = %s",
    ),
    ResetStep(
        "challenge_draft_evidences",
        """
        delete from app.challenge_draft_evidences cde
        using app.challenge_drafts cd
        where cde.challenge_draft_id = cd.id and cd.project_id = %s
        """,
    ),
    ResetStep("challenge_drafts", "delete from app.challenge_drafts where project_id = %s"),
    ResetStep(
        "match_evidences",
        """
        delete from app.match_evidences me
        using app.match_results mr
        where me.match_result_id = mr.id and mr.project_id = %s
        """,
    ),
    ResetStep(
        "match_overrides",
        """
        delete from app.match_overrides mo
        using app.match_results mr
        where mo.match_result_id = mr.id and mr.project_id = %s
        """,
    ),
    ResetStep("match_results", "delete from app.match_results where project_id = %s"),
    ResetStep(
        "risk_evidences",
        """
        delete from app.risk_evidences re
        using app.risks r
        where re.risk_id = r.id and r.project_id = %s
        """,
    ),
    ResetStep(
        "risk_reviews",
        """
        delete from app.risk_reviews rr
        using app.risks r
        where rr.risk_id = r.id and r.project_id = %s
        """,
    ),
    ResetStep("risks", "delete from app.risks where project_id = %s"),
    ResetStep(
        "decision_evidences",
        """
        delete from app.decision_evidences de
        using app.decisions d
        where de.decision_id = d.id and d.project_id = %s
        """,
    ),
    ResetStep("decisions", "delete from app.decisions where project_id = %s"),
    ResetStep("market_checks", "delete from app.market_checks where project_id = %s"),
    ResetStep("graph_edges", "delete from app.graph_edges where project_id = %s"),
    ResetStep("graph_nodes", "delete from app.graph_nodes where project_id = %s"),
    ResetStep(
        "report_evidences",
        """
        delete from app.report_evidences re
        using app.report_sections rs, app.reports r
        where re.report_section_id = rs.id and rs.report_id = r.id and r.project_id = %s
        """,
    ),
    ResetStep(
        "analysis_run_report_links",
        "update app.analysis_runs set report_id = null where project_id = %s",
    ),
    ResetStep(
        "report_analysis_links",
        "update app.reports set analysis_run_id = null where project_id = %s",
    ),
    ResetStep(
        "analysis_output_ai_run_evidences",
        """
        with scoped as (select %s::uuid as project_id)
        delete from app.ai_run_evidences are
        using app.ai_runs ar, app.tasks t, scoped
        where are.ai_run_id = ar.id and ar.task_id = t.id
          and (
            (t.target_type = 'PROJECT' and t.target_id = scoped.project_id)
            or (t.target_type = 'ANALYSIS_RUN' and exists (
                select 1 from app.analysis_runs analysis
                where analysis.id = t.target_id and analysis.project_id = scoped.project_id
            ))
            or (t.target_type = 'REPORT' and exists (
                select 1 from app.reports report
                where report.id = t.target_id and report.project_id = scoped.project_id
            ))
          )
        """,
    ),
    ResetStep(
        "analysis_output_ai_runs",
        """
        with scoped as (select %s::uuid as project_id)
        delete from app.ai_runs ar
        using app.tasks t, scoped
        where ar.task_id = t.id
          and (
            (t.target_type = 'PROJECT' and t.target_id = scoped.project_id)
            or (t.target_type = 'ANALYSIS_RUN' and exists (
                select 1 from app.analysis_runs analysis
                where analysis.id = t.target_id and analysis.project_id = scoped.project_id
            ))
            or (t.target_type = 'REPORT' and exists (
                select 1 from app.reports report
                where report.id = t.target_id and report.project_id = scoped.project_id
            ))
          )
        """,
    ),
    ResetStep(
        "analysis_output_task_parent_links",
        """
        with scoped as (select %s::uuid as project_id)
        update app.tasks child set parent_task_id = null
        where child.parent_task_id in (
            select parent.id from app.tasks parent, scoped
            where (parent.target_type = 'PROJECT' and parent.target_id = scoped.project_id)
               or (parent.target_type = 'ANALYSIS_RUN' and exists (
                    select 1 from app.analysis_runs analysis
                    where analysis.id = parent.target_id
                      and analysis.project_id = scoped.project_id
               ))
               or (parent.target_type = 'REPORT' and exists (
                    select 1 from app.reports report
                    where report.id = parent.target_id and report.project_id = scoped.project_id
               ))
        )
        """,
    ),
    ResetStep(
        "analysis_run_task_links",
        """
        with scoped as (select %s::uuid as project_id)
        update app.analysis_runs analysis set task_id = null
        from app.tasks task, scoped
        where analysis.task_id = task.id and analysis.project_id = scoped.project_id
        """,
    ),
    ResetStep(
        "analysis_output_task_events",
        """
        with scoped as (select %s::uuid as project_id)
        delete from app.task_events event
        using app.tasks task, scoped
        where event.task_id = task.id
          and (
            (task.target_type = 'PROJECT' and task.target_id = scoped.project_id)
            or (task.target_type = 'ANALYSIS_RUN' and exists (
                select 1 from app.analysis_runs analysis
                where analysis.id = task.target_id and analysis.project_id = scoped.project_id
            ))
            or (task.target_type = 'REPORT' and exists (
                select 1 from app.reports report
                where report.id = task.target_id and report.project_id = scoped.project_id
            ))
          )
        """,
    ),
    ResetStep(
        "analysis_output_tasks",
        """
        with scoped as (select %s::uuid as project_id)
        delete from app.tasks task
        using scoped
        where (task.target_type = 'PROJECT' and task.target_id = scoped.project_id)
           or (task.target_type = 'ANALYSIS_RUN' and exists (
                select 1 from app.analysis_runs analysis
                where analysis.id = task.target_id and analysis.project_id = scoped.project_id
           ))
           or (task.target_type = 'REPORT' and exists (
                select 1 from app.reports report
                where report.id = task.target_id and report.project_id = scoped.project_id
           ))
        """,
    ),
    ResetStep(
        "report_sections",
        """
        delete from app.report_sections rs
        using app.reports r
        where rs.report_id = r.id and r.project_id = %s
        """,
    ),
    ResetStep("reports", "delete from app.reports where project_id = %s"),
    ResetStep(
        "analysis_snapshots",
        """
        delete from app.analysis_snapshots snapshot
        using app.analysis_runs ar
        where snapshot.analysis_run_id = ar.id and ar.project_id = %s
        """,
    ),
    ResetStep("analysis_runs", "delete from app.analysis_runs where project_id = %s"),
    ResetStep("project_fields", "delete from app.project_fields where project_id = %s"),
    ResetStep(
        "requirement_evidences",
        """
        delete from app.requirement_evidences re
        using app.requirements r
        where re.requirement_id = r.id and r.project_id = %s
        """,
    ),
    ResetStep("requirements", "delete from app.requirements where project_id = %s"),
    ResetStep("project_comments", "delete from app.project_comments where project_id = %s"),
    ResetStep("work_items", "delete from app.work_items where project_id = %s"),
    ResetStep("notifications", "delete from app.notifications where project_id = %s"),
    ResetStep(
        "quote_scenario_links",
        "update app.quote_scenarios set parent_scenario_id = null where project_id = %s",
    ),
    ResetStep("quote_scenarios", "delete from app.quote_scenarios where project_id = %s"),
    ResetStep("integration_runs", "delete from app.integration_runs where project_id = %s"),
)


def _plain_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重置一个测试项目的派生分析数据")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--project-id", type=UUID, help="要重置的项目 UUID")
    source.add_argument("--document-version-id", type=UUID, help="通过项目内文档版本定位项目")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行重置；未传时只预览受影响的数据量",
    )
    return parser.parse_args()


def _resolve_project(cur: psycopg.Cursor, args: argparse.Namespace) -> tuple[UUID, str, str]:
    if args.project_id:
        cur.execute(
            """
            select id, name, code
            from app.tender_projects
            where id = %s and deleted_at is null
            """,
            (args.project_id,),
        )
    else:
        cur.execute(
            """
            select p.id, p.name, p.code
            from app.document_versions dv
            join app.documents d on d.id = dv.document_id
            join app.tender_projects p on p.id = d.project_id
            where dv.id = %s and d.deleted_at is null and p.deleted_at is null
            """,
            (args.document_version_id,),
        )
    row = cur.fetchone()
    if row is None:
        raise ValueError("未找到有效的项目；请检查项目或文档版本 UUID。")
    return row[0], row[1], row[2]


def _assert_not_running(cur: psycopg.Cursor, project_id: UUID) -> None:
    cur.execute(
        """
        select count(*)
        from app.analysis_runs
        where project_id = %s and status = any(%s)
        """,
        (project_id, list(_ACTIVE_ANALYSIS_STATUSES)),
    )
    active_analysis_count = cur.fetchone()[0]
    cur.execute(
        """
        select count(*)
        from app.tasks task
        where task.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN_REVIEW')
          and (
            (task.target_type = 'PROJECT' and task.target_id = %s)
            or (task.target_type = 'ANALYSIS_RUN' and exists (
                select 1 from app.analysis_runs analysis
                where analysis.id = task.target_id and analysis.project_id = %s
            ))
            or (task.target_type = 'REPORT' and exists (
                select 1 from app.reports report
                where report.id = task.target_id and report.project_id = %s
            ))
          )
        """,
        (project_id, project_id, project_id),
    )
    active_task_count = cur.fetchone()[0]
    if active_analysis_count or active_task_count:
        raise RuntimeError(
            "项目仍有运行中的分析或任务，拒绝清理以避免与 Worker 并发写入。"
        )


def _preview(cur: psycopg.Cursor, project_id: UUID) -> list[tuple[str, int]]:
    counters: Sequence[tuple[str, str]] = (
        ("需求", "select count(*) from app.requirements where project_id = %s"),
        ("项目字段", "select count(*) from app.project_fields where project_id = %s"),
        ("匹配结果", "select count(*) from app.match_results where project_id = %s"),
        ("风险", "select count(*) from app.risks where project_id = %s"),
        ("决策", "select count(*) from app.decisions where project_id = %s"),
        ("报告", "select count(*) from app.reports where project_id = %s"),
        ("分析运行", "select count(*) from app.analysis_runs where project_id = %s"),
        ("Agent 运行", "select count(*) from app.agent_runs where project_id = %s"),
        ("竞争分析", "select count(*) from app.competitive_analyses where project_id = %s"),
        (
            "派生任务",
            """
            select count(*) from app.tasks task
            where (task.target_type = 'PROJECT' and task.target_id = %s)
               or (task.target_type = 'ANALYSIS_RUN' and exists (
                    select 1 from app.analysis_runs analysis
                    where analysis.id = task.target_id and analysis.project_id = %s
               ))
               or (task.target_type = 'REPORT' and exists (
                    select 1 from app.reports report
                    where report.id = task.target_id and report.project_id = %s
               ))
            """,
        ),
    )
    results: list[tuple[str, int]] = []
    for label, statement in counters:
        parameters = (
            (project_id, project_id, project_id)
            if label == "派生任务"
            else (project_id,)
        )
        cur.execute(statement, parameters)
        results.append((label, cur.fetchone()[0]))
    return results


def reset_project(database_url: str, args: argparse.Namespace) -> int:
    with psycopg.connect(_plain_database_url(database_url), autocommit=False) as conn:
        with conn.cursor() as cur:
            project_id, name, code = _resolve_project(cur, args)
            _assert_not_running(cur, project_id)
            print(f"目标项目: {name} ({code})")
            print(f"项目 ID: {project_id}")
            print("将保留：项目、源文档、MinerU 节点、Evidence、企业材料、规则配置。")
            print("将清理的派生数据：")
            for label, count in _preview(cur, project_id):
                print(f"  {label}: {count}")

            if not args.apply:
                conn.rollback()
                print("预览完成。确认执行请追加 --apply。")
                return 0

            changed: list[tuple[str, int]] = []
            for step in _RESET_STEPS:
                cur.execute(step.statement, (project_id,))
                if cur.rowcount:
                    changed.append((step.name, cur.rowcount))
        conn.commit()

    print("已重置派生分析数据：")
    if not changed:
        print("  没有需要清理的记录。")
    else:
        for name, row_count in changed:
            print(f"  {name}: {row_count}")
    return 0


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    if not settings.database_url:
        print("DATABASE_URL 未配置", file=sys.stderr)
        return 1
    try:
        return reset_project(settings.database_url, args)
    except (ValueError, RuntimeError, psycopg.Error) as exc:
        print(f"重置失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
