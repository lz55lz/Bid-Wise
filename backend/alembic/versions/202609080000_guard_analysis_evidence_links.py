"""Guard evidence links referenced by active analysis snapshots."""

from alembic import op


revision = "202609080000"
down_revision = "202609070000"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        create or replace function app.guard_active_analysis_inputs() returns trigger as $$
        declare
          input_id uuid;
          input_project_id uuid;
          input_requirement_id uuid;
          input_material_id uuid;
        begin
          if tg_table_name = 'enterprise_materials' then
            input_id := case when tg_op = 'DELETE' then old.id else new.id end;
          elsif tg_table_name = 'requirements' then
            input_project_id := case when tg_op = 'DELETE' then old.project_id else new.project_id end;
          elsif tg_table_name = 'tender_projects' then
            input_project_id := case when tg_op = 'DELETE' then old.id else new.id end;
          elsif tg_table_name = 'requirement_evidences' then
            input_requirement_id := case when tg_op = 'DELETE' then old.requirement_id else new.requirement_id end;
          elsif tg_table_name = 'material_documents' then
            input_material_id := case when tg_op = 'DELETE' then old.material_id else new.material_id end;
          end if;

          if tg_table_name = 'enterprise_materials' and exists (
            select 1 from app.analysis_runs ar join app.analysis_snapshots s on s.analysis_run_id = ar.id
            where ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
              and s.enterprise_material_ids ? input_id::text
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: enterprise material is referenced by an active analysis';
          end if;
          if tg_table_name in ('requirements', 'tender_projects') and exists (
            select 1 from app.analysis_runs ar
            where ar.project_id = input_project_id and ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: project input is referenced by an active analysis';
          end if;
          if tg_table_name = 'rule_versions' and exists (
            select 1 from app.analysis_runs where status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: rule version is referenced by an active analysis';
          end if;
          if tg_table_name = 'requirement_evidences' and exists (
            select 1 from app.requirements r join app.analysis_runs ar on ar.project_id = r.project_id
            where r.id = input_requirement_id
              and ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: requirement evidence is referenced by an active analysis';
          end if;
          if tg_table_name = 'material_documents' and exists (
            select 1 from app.analysis_runs ar join app.analysis_snapshots s on s.analysis_run_id = ar.id
            where s.enterprise_material_ids ? input_material_id::text
              and ar.status in ('QUEUED', 'RUNNING', 'WAITING_HUMAN')
          ) then
            raise exception 'ANALYSIS_INPUT_LOCKED: material evidence is referenced by an active analysis';
          end if;
          if tg_op = 'DELETE' then return old; end if;
          return new;
        end;
        $$ language plpgsql;

        create trigger trg_guard_analysis_requirement_evidence
          before insert or update or delete on app.requirement_evidences
          for each row execute function app.guard_active_analysis_inputs();
        create trigger trg_guard_analysis_material_document
          before insert or update or delete on app.material_documents
          for each row execute function app.guard_active_analysis_inputs();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        drop trigger trg_guard_analysis_material_document on app.material_documents;
        drop trigger trg_guard_analysis_requirement_evidence on app.requirement_evidences;
        """
    )
