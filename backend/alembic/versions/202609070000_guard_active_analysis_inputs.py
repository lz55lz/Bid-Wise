"""Prevent mutation of inputs referenced by an active analysis run.

This makes the existing domain services safe to read their normal fact tables
while an AnalysisRun is executing: inputs cannot drift after its snapshot has
been accepted.
"""

from alembic import op


revision = "202609070000"
down_revision = "202609060000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create function app.guard_active_analysis_inputs() returns trigger as $$
        begin
          if tg_table_name = 'enterprise_materials' and exists (
            select 1 from app.analysis_runs ar join app.analysis_snapshots s on s.analysis_run_id = ar.id
            where ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
              and s.enterprise_material_ids ? old.id::text
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: enterprise material is referenced by an active analysis';
          end if;
          if tg_table_name = 'requirements' and exists (
            select 1 from app.analysis_runs ar
            where ar.project_id = old.project_id and ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: project requirement is referenced by an active analysis';
          end if;
          if tg_table_name = 'tender_projects' and exists (
            select 1 from app.analysis_runs ar
            where ar.project_id = old.id and ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: project is referenced by an active analysis';
          end if;
          if tg_table_name = 'rule_versions' and exists (
            select 1 from app.analysis_runs where status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: rule versions are referenced by an active analysis';
          end if;
          return old;
        end;
        $$ language plpgsql;

        create trigger trg_guard_analysis_material before update or delete on app.enterprise_materials
          for each row execute function app.guard_active_analysis_inputs();
        create trigger trg_guard_analysis_requirement before update or delete on app.requirements
          for each row execute function app.guard_active_analysis_inputs();
        create trigger trg_guard_analysis_project before update or delete on app.tender_projects
          for each row execute function app.guard_active_analysis_inputs();
        create trigger trg_guard_analysis_rule_version before update or delete on app.rule_versions
          for each row execute function app.guard_active_analysis_inputs();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        drop trigger trg_guard_analysis_rule_version on app.rule_versions;
        drop trigger trg_guard_analysis_project on app.tender_projects;
        drop trigger trg_guard_analysis_requirement on app.requirements;
        drop trigger trg_guard_analysis_material on app.enterprise_materials;
        drop function app.guard_active_analysis_inputs();
        """
    )
