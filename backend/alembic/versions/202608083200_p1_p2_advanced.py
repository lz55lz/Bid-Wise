"""Create P1/P2 advanced-domain tables."""

from alembic import op

revision = "202608083200"
down_revision = "202608083100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        create table app.knowledge_entries (
          id uuid primary key default gen_random_uuid(), knowledge_type varchar(16) not null check (knowledge_type in ('LEGAL','CASE')),
          title varchar(512) not null, authority varchar(256), source_reference varchar(1024) not null,
          created_at timestamptz not null default now(), created_by uuid not null references app.users(id),
          updated_at timestamptz not null default now(), deleted_at timestamptz
        );
        create table app.knowledge_versions (
          id uuid primary key default gen_random_uuid(), knowledge_entry_id uuid not null references app.knowledge_entries(id),
          version_no integer not null check (version_no > 0), status varchar(16) not null check (status in ('DRAFT','PUBLISHED','ARCHIVED')),
          content text not null, issued_on date, effective_on date, citation_note text, published_at timestamptz,
          published_by uuid references app.users(id), created_at timestamptz not null default now(), created_by uuid not null references app.users(id),
          constraint uq_knowledge_versions unique (knowledge_entry_id, version_no)
        );
        create index ix_knowledge_versions_status on app.knowledge_versions (status, published_at desc);

        create table app.competitive_analyses (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          requirement_id uuid references app.requirements(id), status varchar(16) not null check (status in ('QUEUED','RUNNING','READY','FAILED')),
          method varchar(32) not null, summary text, created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id), completed_at timestamptz
        );
        create table app.competitive_analysis_evidences (
          analysis_id uuid not null references app.competitive_analyses(id), evidence_id uuid not null references app.evidences(id), primary key (analysis_id,evidence_id)
        );
        create table app.competitive_findings (
          id uuid primary key default gen_random_uuid(), analysis_id uuid not null references app.competitive_analyses(id),
          category varchar(48) not null check (category in ('BRAND_OR_PARAMETER','EXCESSIVE_QUALIFICATION','GEOGRAPHIC_RESTRICTION','UNIQUE_SUPPLY','INCONSISTENT_REQUIREMENT','OTHER')),
          title varchar(512) not null, description text not null, confidence numeric(5,4),
          status varchar(16) not null check (status in ('PENDING','CONFIRMED','RESOLVED','FALSE_POSITIVE','IGNORED')),
          resolution text, reviewed_by uuid references app.users(id), reviewed_at timestamptz, created_at timestamptz not null default now()
        );
        create table app.competitive_finding_evidences (
          finding_id uuid not null references app.competitive_findings(id), evidence_id uuid not null references app.evidences(id), primary key (finding_id,evidence_id)
        );
        create table app.competitive_finding_knowledge (
          finding_id uuid not null references app.competitive_findings(id), knowledge_version_id uuid not null references app.knowledge_versions(id), primary key (finding_id,knowledge_version_id)
        );
        create index ix_competitive_analyses_project on app.competitive_analyses (project_id, created_at desc);

        create table app.challenge_drafts (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          title varchar(512) not null, subject text not null, fact_statement text not null, requested_action text not null,
          status varchar(16) not null check (status in ('DRAFT','UNDER_REVIEW','APPROVED','REJECTED')),
          review_note text, reviewed_by uuid references app.users(id), reviewed_at timestamptz,
          docx_object_key varchar(1024), pdf_object_key varchar(1024), created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id), updated_at timestamptz not null default now()
        );
        create table app.challenge_draft_evidences (
          challenge_draft_id uuid not null references app.challenge_drafts(id), evidence_id uuid not null references app.evidences(id), primary key (challenge_draft_id,evidence_id)
        );
        create index ix_challenge_drafts_project on app.challenge_drafts (project_id, created_at desc);

        create table app.quote_scenarios (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          parent_scenario_id uuid references app.quote_scenarios(id), name varchar(256) not null, version_no integer not null check (version_no > 0),
          status varchar(16) not null check (status in ('DRAFT','LOCKED','ARCHIVED')),
          cost_excluding_tax numeric(18,2) not null check (cost_excluding_tax >= 0), tax_rate numeric(7,4) not null check (tax_rate >= 0 and tax_rate <= 1),
          target_margin_rate numeric(7,4) not null check (target_margin_rate >= 0 and target_margin_rate < 1), risk_adjustment numeric(18,2) not null default 0,
          expected_score numeric(9,4), assumptions jsonb not null default '{}'::jsonb, calculations jsonb not null default '{}'::jsonb,
          locked_at timestamptz, locked_by uuid references app.users(id), created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id), updated_at timestamptz not null default now(),
          constraint uq_quote_scenarios_version unique (project_id,name,version_no)
        );

        create table app.project_comments (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id), target_type varchar(64), target_id uuid,
          content text not null, created_at timestamptz not null default now(), created_by uuid not null references app.users(id)
        );
        create table app.work_items (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id), title varchar(512) not null,
          description text, status varchar(16) not null check (status in ('OPEN','IN_PROGRESS','DONE','CANCELLED')), assignee_id uuid references app.users(id),
          due_at timestamptz, target_type varchar(64), target_id uuid, closing_note text, created_at timestamptz not null default now(),
          created_by uuid not null references app.users(id), updated_at timestamptz not null default now()
        );
        create index ix_project_comments_project on app.project_comments (project_id, created_at desc);
        create index ix_work_items_project on app.work_items (project_id, status, due_at);
        create table app.notifications (
          id uuid primary key default gen_random_uuid(), user_id uuid not null references app.users(id), project_id uuid references app.tender_projects(id),
          notification_type varchar(64) not null, payload jsonb not null default '{}'::jsonb, read_at timestamptz, created_at timestamptz not null default now()
        );
        create index ix_notifications_user on app.notifications (user_id, read_at, created_at desc);

        create table app.market_checks (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          requirement_id uuid references app.requirements(id), evidence_id uuid references app.evidences(id), parameter text not null,
          source_name varchar(256) not null, source_reference varchar(1024) not null, excerpt text not null,
          conclusion varchar(24) not null check (conclusion in ('SUPPORTED','NOT_SUPPORTED','INCONCLUSIVE')), note text,
          created_at timestamptz not null default now(), created_by uuid not null references app.users(id),
          constraint ck_market_checks_subject check (requirement_id is not null or evidence_id is not null)
        );

        create table app.graph_nodes (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id), entity_type varchar(32) not null,
          source_object_id varchar(64) not null, label varchar(512) not null, attributes jsonb not null default '{}'::jsonb,
          source_evidence_id uuid references app.evidences(id), created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
          constraint uq_graph_node_source unique (project_id,entity_type,source_object_id)
        );
        create table app.graph_edges (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          from_node_id uuid not null references app.graph_nodes(id), to_node_id uuid not null references app.graph_nodes(id), relation_type varchar(64) not null,
          source_evidence_id uuid references app.evidences(id), created_at timestamptz not null default now(), created_by uuid not null references app.users(id),
          constraint uq_graph_edge unique (project_id,from_node_id,to_node_id,relation_type)
        );
        create index ix_graph_nodes_project on app.graph_nodes (project_id, entity_type);
        create index ix_graph_edges_project on app.graph_edges (project_id, from_node_id, to_node_id);

        create table app.agent_runs (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          workflow varchar(64) not null check (workflow in ('BID_READINESS_REVIEW','COMPLIANCE_REVIEW','MARKET_REVIEW')),
          status varchar(16) not null check (status in ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')), goal text not null,
          input_hash varchar(64) not null, result jsonb not null default '{}'::jsonb, error_code varchar(80), error_message text,
          created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz, created_by uuid not null references app.users(id)
        );
        create table app.agent_run_evidences (
          agent_run_id uuid not null references app.agent_runs(id), evidence_id uuid not null references app.evidences(id), primary key (agent_run_id,evidence_id)
        );

        create table app.integration_connectors (
          code varchar(40) primary key, name varchar(128) not null, capabilities jsonb not null default '[]'::jsonb,
          is_enabled boolean not null default false, created_at timestamptz not null default now(), updated_at timestamptz not null default now()
        );
        insert into app.integration_connectors (code,name,capabilities,is_enabled) values
          ('ERP','ERP 受控连接器','["LOOKUP","EXPORT"]'::jsonb,false),
          ('CRM','CRM 受控连接器','["LOOKUP","EXPORT"]'::jsonb,false),
          ('PUBLIC_RESOURCE','公共资源交易受控连接器','["LOOKUP"]'::jsonb,false);
        create table app.integration_runs (
          id uuid primary key default gen_random_uuid(), project_id uuid not null references app.tender_projects(id),
          connector_code varchar(40) not null references app.integration_connectors(code), operation varchar(16) not null check (operation in ('LOOKUP','EXPORT')),
          status varchar(16) not null check (status in ('QUEUED','RUNNING','SUCCEEDED','FAILED','CANCELLED')), input_hash varchar(64) not null,
          result_summary jsonb not null default '{}'::jsonb, external_reference varchar(256), error_code varchar(80), error_message text,
          created_at timestamptz not null default now(), started_at timestamptz, completed_at timestamptz, created_by uuid not null references app.users(id)
        );
        create index ix_integration_runs_project on app.integration_runs (project_id, created_at desc);
    """)


def downgrade() -> None:
    op.execute("""
        drop table app.integration_runs; drop table app.integration_connectors; drop table app.agent_run_evidences; drop table app.agent_runs;
        drop table app.graph_edges; drop table app.graph_nodes; drop table app.market_checks; drop table app.notifications; drop table app.work_items;
        drop table app.project_comments; drop table app.quote_scenarios; drop table app.challenge_draft_evidences; drop table app.challenge_drafts;
        drop table app.competitive_finding_knowledge; drop table app.competitive_finding_evidences; drop table app.competitive_findings;
        drop table app.competitive_analysis_evidences; drop table app.competitive_analyses; drop table app.knowledge_versions; drop table app.knowledge_entries;
    """)
