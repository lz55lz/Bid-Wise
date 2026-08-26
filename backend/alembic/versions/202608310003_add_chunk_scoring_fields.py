"""add_chunk_scoring_fields: keyword_score + tender_req_candidate 到 bid_doc_chunk

配合 clean_node 整合旧 KeywordScoringService + section 级候选限流。
"""
from alembic import op
import sqlalchemy as sa

revision = '202608310003'
down_revision = '202608310002'


def upgrade():
    op.add_column('bid_doc_chunk', sa.Column('keyword_score', sa.Integer, default=0))
    op.add_column('bid_doc_chunk', sa.Column('tender_req_candidate', sa.Boolean, default=False))
    op.create_index('idx_doc_chunk_tender', 'bid_doc_chunk', ['tender_req_candidate'],
                    postgresql_where=sa.text('tender_req_candidate = true'))
    op.create_index('idx_doc_chunk_score', 'bid_doc_chunk', ['keyword_score'])


def downgrade():
    op.drop_index('idx_doc_chunk_score', 'bid_doc_chunk')
    op.drop_index('idx_doc_chunk_tender', 'bid_doc_chunk')
    op.drop_column('bid_doc_chunk', 'tender_req_candidate')
    op.drop_column('bid_doc_chunk', 'keyword_score')
