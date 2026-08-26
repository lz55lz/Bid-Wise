"""bid_tag_system: 标签体系 + bid_doc_chunk 扩展 + 管线表

Revision: 202608310001
Down_revision: 202608310000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision = '202608310001'
down_revision = '202608310000'


def upgrade():
    # pgvector + pg_trgm 扩展
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")

    # 1. bid_tag_category
    op.create_table(
        'bid_tag_category',
        sa.Column('category_code', sa.String(20), primary_key=True),
        sa.Column('category_name', sa.String(100), nullable=False),
        sa.Column('category_desc', sa.Text),
        sa.Column('sort_order', sa.Integer, default=0),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 2. bid_tag_level
    op.create_table(
        'bid_tag_level',
        sa.Column('level_code', sa.String(10), primary_key=True),
        sa.Column('level_name', sa.String(50), nullable=False),
        sa.Column('level_desc', sa.Text),
        sa.Column('sort_order', sa.Integer, default=0),
    )

    # 3. bid_tag_dict（150 个标签）
    op.create_table(
        'bid_tag_dict',
        sa.Column('tag_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('tag_code', sa.String(80), nullable=False, unique=True),
        sa.Column('tag_name', sa.String(200), nullable=False),
        sa.Column('category_code', sa.String(20), nullable=False),
        sa.Column('level_code', sa.String(10), nullable=False),
        sa.Column('data_type', sa.String(30), nullable=False),
        sa.Column('is_required', sa.Boolean, default=False),
        sa.Column('is_multi_value', sa.Boolean, default=False),
        sa.Column('extraction_prompt', sa.Text),
        sa.Column('value_example', sa.Text),
        sa.Column('validation_regex', sa.Text),
        sa.Column('remark', sa.Text),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_tag_dict_cat', 'bid_tag_dict', ['category_code'])
    op.create_index('idx_tag_dict_level', 'bid_tag_dict', ['level_code'])
    op.create_index('idx_tag_dict_active', 'bid_tag_dict', ['is_active'])

    # 4. bid_tag_relation
    op.create_table(
        'bid_tag_relation',
        sa.Column('relation_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('source_tag_code', sa.String(80), nullable=False),
        sa.Column('target_tag_code', sa.String(80), nullable=False),
        sa.Column('relation_type', sa.String(30), nullable=False),
        sa.Column('relation_desc', sa.Text),
        sa.Column('rule_json', JSONB),
        sa.Column('priority', sa.String(10), default='P1'),
        sa.Column('is_active', sa.Boolean, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_rel_src', 'bid_tag_relation', ['source_tag_code'])
    op.create_index('idx_rel_tgt', 'bid_tag_relation', ['target_tag_code'])
    op.create_index('idx_rel_type', 'bid_tag_relation', ['relation_type'])

    # 5. bid_document（独立表，不依赖现有 documents）
    op.create_table(
        'bid_document',
        sa.Column('doc_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_name', sa.String(500), nullable=False),
        sa.Column('doc_type', sa.String(30)),
        sa.Column('doc_url', sa.Text),
        sa.Column('project_code', sa.String(100)),
        sa.Column('file_hash', sa.String(64)),
        sa.Column('parse_status', sa.String(20), default='pending'),
        sa.Column('raw_text_path', sa.Text),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 6. bid_doc_chunk（含双层标签 + 向量，embedding 用 1024 维匹配现有 BGE-M3）
    op.create_table(
        'bid_doc_chunk',
        sa.Column('chunk_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.BigInteger, nullable=False),
        sa.Column('chunk_index', sa.Integer, nullable=False),
        sa.Column('page_no', sa.Integer),
        sa.Column('section_path', sa.Text),
        sa.Column('chunk_text', sa.Text, nullable=False),
        sa.Column('chunk_type', sa.String(20), default='paragraph'),
        sa.Column('category_codes', ARRAY(sa.String())),
        sa.Column('candidate_tags', ARRAY(sa.String())),
        sa.Column('prev_chunk_id', sa.BigInteger),
        sa.Column('next_chunk_id', sa.BigInteger),
        # embedding 用 1024 维（匹配现有 BGE-M3 模型）
        sa.Column('embedding', sa.Text),  # 先存 text，迁移后改类型
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('doc_id', 'chunk_index'),
    )
    # 转换并改为 vector 类型
    op.execute("""
        ALTER TABLE bid_doc_chunk
        ALTER COLUMN embedding TYPE text,
        ALTER COLUMN embedding SET NOT NULL
    """)
    op.execute("""
        ALTER TABLE bid_doc_chunk
        ALTER COLUMN embedding TYPE vector(1024)
        USING embedding::vector(1024)
    """)
    op.create_index('idx_doc_chunk_doc', 'bid_doc_chunk', ['doc_id'])
    op.create_index('idx_doc_chunk_cat', 'bid_doc_chunk', ['doc_id', 'category_codes'])
    op.create_index('idx_doc_chunk_tags', 'bid_doc_chunk', ['candidate_tags'], postgresql_using='gin')
    op.execute("""
        CREATE INDEX idx_doc_chunk_emb
        ON bid_doc_chunk USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100)
    """)

    # 7. bid_document_tag
    op.create_table(
        'bid_document_tag',
        sa.Column('id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.BigInteger, nullable=False),
        sa.Column('tag_id', sa.BigInteger, nullable=False),
        sa.Column('tag_value', sa.Text),
        sa.Column('tag_value_json', JSONB),
        sa.Column('source_text', sa.Text),
        sa.Column('source_chunk_id', sa.BigInteger),
        sa.Column('source_page', sa.Integer),
        sa.Column('confidence', sa.Numeric(5, 2)),
        sa.Column('extract_method', sa.String(20)),
        sa.Column('llm_model', sa.String(50)),
        sa.Column('extracted_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('reviewed', sa.Boolean, default=False),
        sa.Column('reviewer', sa.String(100)),
        sa.Column('reviewed_at', sa.DateTime(timezone=True)),
        sa.Column('remark', sa.Text),
        sa.UniqueConstraint('doc_id', 'tag_id'),
    )
    op.create_index('idx_doc_tag_doc', 'bid_document_tag', ['doc_id'])
    op.create_index('idx_doc_tag_val', 'bid_document_tag', ['tag_value_json'], postgresql_using='gin')

    # 8. bid_task_log
    op.create_table(
        'bid_task_log',
        sa.Column('task_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.BigInteger, nullable=False),
        sa.Column('thread_id', sa.String(100)),
        sa.Column('stage', sa.String(50), nullable=False),
        sa.Column('node_name', sa.String(50)),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('attempt', sa.Integer, default=1),
        sa.Column('max_attempts', sa.Integer, default=3),
        sa.Column('started_at', sa.DateTime(timezone=True)),
        sa.Column('finished_at', sa.DateTime(timezone=True)),
        sa.Column('duration_ms', sa.Integer),
        sa.Column('input_summary', sa.Text),
        sa.Column('output_summary', sa.Text),
        sa.Column('error_msg', sa.Text),
        sa.Column('payload', JSONB),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_task_log_doc', 'bid_task_log', ['doc_id', 'stage'])
    op.create_index('idx_task_log_status', 'bid_task_log', ['status'])

    # 9. bid_risk
    op.create_table(
        'bid_risk',
        sa.Column('risk_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.BigInteger, nullable=False),
        sa.Column('risk_type', sa.String(40), nullable=False),
        sa.Column('risk_level', sa.String(10), nullable=False),
        sa.Column('risk_title', sa.String(500), nullable=False),
        sa.Column('risk_desc', sa.Text),
        sa.Column('related_tags', ARRAY(sa.String())),
        sa.Column('source_chunks', ARRAY(sa.BigInteger)),
        sa.Column('suggestion', sa.Text),
        sa.Column('confidence', sa.Numeric(5, 2)),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('idx_risk_doc', 'bid_risk', ['doc_id', 'risk_level', 'risk_type'])

    # 10. bid_report
    op.create_table(
        'bid_report',
        sa.Column('report_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('doc_id', sa.BigInteger, nullable=False, unique=True),
        sa.Column('decision', sa.String(20)),
        sa.Column('overall_score', sa.Numeric(5, 2)),
        sa.Column('qualification_score', sa.Numeric(5, 2)),
        sa.Column('risk_score', sa.Numeric(5, 2)),
        sa.Column('trap_score', sa.Numeric(5, 2)),
        sa.Column('competition_score', sa.Numeric(5, 2)),
        sa.Column('summary', sa.Text),
        sa.Column('report_md', sa.Text),
        sa.Column('report_json', JSONB),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 11. enterprise_profile
    op.create_table(
        'enterprise_profile',
        sa.Column('ep_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('enterprise_name', sa.String(200), nullable=False),
        sa.Column('credit_code', sa.String(18), unique=True),
        sa.Column('qualifications', JSONB),
        sa.Column('past_projects', JSONB),
        sa.Column('financials', JSONB),
        sa.Column('personnel', JSONB),
        sa.Column('awards', JSONB),
        sa.Column('blacklist_status', JSONB),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )

    # 12. competitor_history
    op.create_table(
        'competitor_history',
        sa.Column('comp_id', sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column('enterprise_name', sa.String(200), nullable=False),
        sa.Column('project_name', sa.String(500)),
        sa.Column('project_amount', sa.Numeric(15, 2)),
        sa.Column('bid_amount', sa.Numeric(15, 2)),
        sa.Column('win', sa.Boolean),
        sa.Column('bid_date', sa.Date),
        sa.Column('project_type', sa.String(50)),
        sa.Column('region', sa.String(50)),
    )
    op.create_index('idx_comp_name', 'competitor_history', ['enterprise_name'])
    op.create_index('idx_comp_type', 'competitor_history', ['project_type', 'region'])

    # 视图
    op.execute("""
        CREATE OR REPLACE VIEW v_tag_summary AS
        SELECT
            c.category_code, c.category_name,
            l.level_code, l.level_name,
            COUNT(*) AS tag_count,
            COUNT(*) FILTER (WHERE t.is_required) AS required_count
        FROM bid_tag_category c
        JOIN bid_tag_dict t ON t.category_code = c.category_code
        JOIN bid_tag_level l ON t.level_code = l.level_code
        WHERE t.is_active = TRUE
        GROUP BY c.category_code, c.category_name, l.level_code, l.level_name
        ORDER BY c.sort_order, l.sort_order
    """)


def downgrade():
    for table in [
        'competitor_history', 'enterprise_profile', 'bid_report',
        'bid_risk', 'bid_task_log', 'bid_document_tag',
        'bid_doc_chunk', 'bid_document', 'bid_tag_relation',
        'bid_tag_dict', 'bid_tag_level', 'bid_tag_category',
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP VIEW IF EXISTS v_tag_summary")
