"""Add ON DELETE CASCADE to optimize knowledge deletion.

Revision ID: add_cascade_deletes
Revises:
Create Date: 2026-08-10
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "add_cascade_deletes"
down_revision = "202608091100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # === KnowledgeVersion → KnowledgeEntry (CASCADE) ===
    op.execute("""
        ALTER TABLE app.knowledge_versions
        DROP CONSTRAINT IF EXISTS knowledge_versions_knowledge_entry_id_fkey,
        ADD CONSTRAINT knowledge_versions_knowledge_entry_id_fkey
            FOREIGN KEY (knowledge_entry_id) REFERENCES app.knowledge_entries(id)
            ON DELETE CASCADE
    """)

    # === KnowledgeVersion.source_evidence_id (SET NULL) ===
    op.execute("""
        ALTER TABLE app.knowledge_versions
        DROP CONSTRAINT IF EXISTS knowledge_versions_source_evidence_id_fkey,
        ADD CONSTRAINT knowledge_versions_source_evidence_id_fkey
            FOREIGN KEY (source_evidence_id) REFERENCES app.evidences(id)
            ON DELETE SET NULL
    """)

    # === KnowledgeVersion.source_document_version_id (SET NULL) ===
    op.execute("""
        ALTER TABLE app.knowledge_versions
        DROP CONSTRAINT IF EXISTS knowledge_versions_source_document_version_id_fkey,
        ADD CONSTRAINT knowledge_versions_source_document_version_id_fkey
            FOREIGN KEY (source_document_version_id) REFERENCES app.document_versions(id)
            ON DELETE SET NULL
    """)

    # === DocumentNode → DocumentVersion (CASCADE) ===
    op.execute("""
        ALTER TABLE app.document_nodes
        DROP CONSTRAINT IF EXISTS document_nodes_document_version_id_fkey,
        ADD CONSTRAINT document_nodes_document_version_id_fkey
            FOREIGN KEY (document_version_id) REFERENCES app.document_versions(id)
            ON DELETE CASCADE
    """)

    # === DocumentNode.parent_node_id → DocumentNode (CASCADE) ===
    op.execute("""
        ALTER TABLE app.document_nodes
        DROP CONSTRAINT IF EXISTS document_nodes_parent_node_id_fkey,
        ADD CONSTRAINT document_nodes_parent_node_id_fkey
            FOREIGN KEY (parent_node_id) REFERENCES app.document_nodes(id)
            ON DELETE CASCADE
    """)

    # === Evidence → DocumentVersion (CASCADE) ===
    op.execute("""
        ALTER TABLE app.evidences
        DROP CONSTRAINT IF EXISTS evidences_document_version_id_fkey,
        ADD CONSTRAINT evidences_document_version_id_fkey
            FOREIGN KEY (document_version_id) REFERENCES app.document_versions(id)
            ON DELETE CASCADE
    """)

    # === Evidence → DocumentNode (CASCADE) ===
    op.execute("""
        ALTER TABLE app.evidences
        DROP CONSTRAINT IF EXISTS evidences_document_node_id_fkey,
        ADD CONSTRAINT evidences_document_node_id_fkey
            FOREIGN KEY (document_node_id) REFERENCES app.document_nodes(id)
            ON DELETE CASCADE
    """)

    # === DocumentVersion → Document (CASCADE) ===
    op.execute("""
        ALTER TABLE app.document_versions
        DROP CONSTRAINT IF EXISTS document_versions_document_id_fkey,
        ADD CONSTRAINT document_versions_document_id_fkey
            FOREIGN KEY (document_id) REFERENCES app.documents(id)
            ON DELETE CASCADE
    """)

    # === Task.parent_task_id → Task (CASCADE) ===
    op.execute("""
        ALTER TABLE app.tasks
        DROP CONSTRAINT IF EXISTS tasks_parent_task_id_fkey,
        ADD CONSTRAINT tasks_parent_task_id_fkey
            FOREIGN KEY (parent_task_id) REFERENCES app.tasks(id)
            ON DELETE CASCADE
    """)

    # === TaskEvent → Task (CASCADE) ===
    op.execute("""
        ALTER TABLE app.task_events
        DROP CONSTRAINT IF EXISTS task_events_task_id_fkey,
        ADD CONSTRAINT task_events_task_id_fkey
            FOREIGN KEY (task_id) REFERENCES app.tasks(id)
            ON DELETE CASCADE
    """)

    # === MaterialDocument → EnterpriseMaterial (CASCADE) ===
    op.execute("""
        ALTER TABLE app.material_documents
        DROP CONSTRAINT IF EXISTS material_documents_material_id_fkey,
        ADD CONSTRAINT material_documents_material_id_fkey
            FOREIGN KEY (material_id) REFERENCES app.enterprise_materials(id)
            ON DELETE CASCADE
    """)

    # === MaterialDocument → Document (CASCADE) ===
    op.execute("""
        ALTER TABLE app.material_documents
        DROP CONSTRAINT IF EXISTS material_documents_document_id_fkey,
        ADD CONSTRAINT material_documents_document_id_fkey
            FOREIGN KEY (document_id) REFERENCES app.documents(id)
            ON DELETE CASCADE
    """)

    # === MaterialDocument → DocumentVersion (CASCADE) ===
    op.execute("""
        ALTER TABLE app.material_documents
        DROP CONSTRAINT IF EXISTS material_documents_document_version_id_fkey,
        ADD CONSTRAINT material_documents_document_version_id_fkey
            FOREIGN KEY (document_version_id) REFERENCES app.document_versions(id)
            ON DELETE CASCADE
    """)

    # === AiRun → Task (SET NULL) ===
    op.execute("""
        ALTER TABLE app.ai_runs
        DROP CONSTRAINT IF EXISTS ai_runs_task_id_fkey,
        ADD CONSTRAINT ai_runs_task_id_fkey
            FOREIGN KEY (task_id) REFERENCES app.tasks(id)
            ON DELETE SET NULL
    """)

    # === SearchChunk → Evidence (SET NULL) ===
    op.execute("""
        ALTER TABLE app.search_chunks
        DROP CONSTRAINT IF EXISTS search_chunks_evidence_id_fkey,
        ADD CONSTRAINT search_chunks_evidence_id_fkey
            FOREIGN KEY (evidence_id) REFERENCES app.evidences(id)
            ON DELETE SET NULL
    """)

    # === SearchChunk → DocumentNode (SET NULL) ===
    op.execute("""
        ALTER TABLE app.search_chunks
        DROP CONSTRAINT IF EXISTS search_chunks_source_node_id_fkey,
        ADD CONSTRAINT search_chunks_source_node_id_fkey
            FOREIGN KEY (source_node_id) REFERENCES app.document_nodes(id)
            ON DELETE SET NULL
    """)

    # === SearchChunk → DocumentVersion (SET NULL) ===
    op.execute("""
        ALTER TABLE app.search_chunks
        DROP CONSTRAINT IF EXISTS search_chunks_source_document_version_id_fkey,
        ADD CONSTRAINT search_chunks_source_document_version_id_fkey
            FOREIGN KEY (source_document_version_id) REFERENCES app.document_versions(id)
            ON DELETE SET NULL
    """)

    # === Junction tables → Evidence (CASCADE) ===
    junction_tables = [
        "ai_run_evidences",
        "requirement_evidences",
        "risk_evidences",
        "competitive_finding_evidences",
        "competitive_analysis_evidences",
        "challenge_draft_evidences",
        "match_evidences",
        "report_evidences",
        "decision_evidences",
        "agent_run_evidences",
        "agent_recommendation_evidences",
    ]
    for tbl in junction_tables:
        op.execute(f"""
            ALTER TABLE app.{tbl}
            DROP CONSTRAINT IF EXISTS {tbl}_evidence_id_fkey,
            ADD CONSTRAINT {tbl}_evidence_id_fkey
                FOREIGN KEY (evidence_id) REFERENCES app.evidences(id)
                ON DELETE CASCADE
        """)

    # === Junction tables → parent entity (CASCADE) ===
    parent_cascades = [
        ("ai_run_evidences", "ai_run_id", "ai_runs"),
        ("requirement_evidences", "requirement_id", "requirements"),
        ("risk_evidences", "risk_id", "risks"),
        ("competitive_finding_evidences", "finding_id", "competitive_findings"),
        ("competitive_analysis_evidences", "analysis_id", "competitive_analyses"),
        ("challenge_draft_evidences", "challenge_draft_id", "challenge_drafts"),
        ("match_evidences", "match_result_id", "match_results"),
        ("report_evidences", "report_section_id", "report_sections"),
        ("decision_evidences", "decision_id", "decisions"),
        ("agent_run_evidences", "agent_run_id", "agent_runs"),
        ("agent_recommendation_evidences", "recommendation_id", "agent_recommendations"),
    ]
    for tbl, col, ref_table in parent_cascades:
        op.execute(f"""
            ALTER TABLE app.{tbl}
            DROP CONSTRAINT IF EXISTS {tbl}_{col}_fkey,
            ADD CONSTRAINT {tbl}_{col}_fkey
                FOREIGN KEY ({col}) REFERENCES app.{ref_table}(id)
                ON DELETE CASCADE
        """)

    # === competitive_findings → competitive_analyses (CASCADE) ===
    op.execute("""
        ALTER TABLE app.competitive_findings
        DROP CONSTRAINT IF EXISTS competitive_findings_analysis_id_fkey,
        ADD CONSTRAINT competitive_findings_analysis_id_fkey
            FOREIGN KEY (analysis_id) REFERENCES app.competitive_analyses(id)
            ON DELETE CASCADE
    """)

    # === competitive_finding_knowledge (CASCADE on both sides) ===
    op.execute("""
        ALTER TABLE app.competitive_finding_knowledge
        DROP CONSTRAINT IF EXISTS competitive_finding_knowledge_finding_id_fkey,
        ADD CONSTRAINT competitive_finding_knowledge_finding_id_fkey
            FOREIGN KEY (finding_id) REFERENCES app.competitive_findings(id)
            ON DELETE CASCADE
    """)
    op.execute("""
        ALTER TABLE app.competitive_finding_knowledge
        DROP CONSTRAINT IF EXISTS competitive_finding_knowledge_knowledge_version_id_fkey,
        ADD CONSTRAINT competitive_finding_knowledge_knowledge_version_id_fkey
            FOREIGN KEY (knowledge_version_id) REFERENCES app.knowledge_versions(id)
            ON DELETE CASCADE
    """)

    # === AgentRunStep → AgentRun (CASCADE) ===
    op.execute("""
        ALTER TABLE app.agent_run_steps
        DROP CONSTRAINT IF EXISTS agent_run_steps_agent_run_id_fkey,
        ADD CONSTRAINT agent_run_steps_agent_run_id_fkey
            FOREIGN KEY (agent_run_id) REFERENCES app.agent_runs(id)
            ON DELETE CASCADE
    """)

    # === AgentRecommendation → AgentRun (CASCADE) ===
    op.execute("""
        ALTER TABLE app.agent_recommendations
        DROP CONSTRAINT IF EXISTS agent_recommendations_agent_run_id_fkey,
        ADD CONSTRAINT agent_recommendations_agent_run_id_fkey
            FOREIGN KEY (agent_run_id) REFERENCES app.agent_runs(id)
            ON DELETE CASCADE
    """)

    # === AgentRecommendationEvidence → AgentRecommendation (CASCADE) ===
    op.execute("""
        ALTER TABLE app.agent_recommendation_evidences
        DROP CONSTRAINT IF EXISTS agent_recommendation_evidences_recommendation_id_fkey,
        ADD CONSTRAINT agent_recommendation_evidences_recommendation_id_fkey
            FOREIGN KEY (recommendation_id) REFERENCES app.agent_recommendations(id)
            ON DELETE CASCADE
    """)

    # === Document.current_version_id → DocumentVersion (SET NULL) ===
    # Two constraints exist: documents_current_version_id_fkey (SET NULL) and
    # fk_documents_current_version (RESTRICT). Both must be SET NULL.
    op.execute("""
        ALTER TABLE app.documents
        DROP CONSTRAINT IF EXISTS documents_current_version_id_fkey,
        ADD CONSTRAINT documents_current_version_id_fkey
            FOREIGN KEY (current_version_id) REFERENCES app.document_versions(id)
            ON DELETE SET NULL
    """)
    op.execute("""
        ALTER TABLE app.documents
        DROP CONSTRAINT IF EXISTS fk_documents_current_version,
        ADD CONSTRAINT fk_documents_current_version
            FOREIGN KEY (current_version_id) REFERENCES app.document_versions(id)
            ON DELETE SET NULL
    """)


def downgrade() -> None:
    # Note: downgrade is a no-op for safety; restore constraints manually if needed
    pass
