from uuid import UUID

from app.db.models import SearchChunk
from app.integrations.vector_store import VectorSearchHit
from app.services.tender_agent_context_retriever import TenderAgentContextRetriever

_PROJECT_ID = UUID("00000000-0000-0000-0000-000000000201")
_VERSION_ID = UUID("00000000-0000-0000-0000-000000000202")


class FakeEmbeddingClient:
    def embed(self, contents):
        assert len(contents) == 7
        return [[float(index)] for index, _ in enumerate(contents, start=1)]


class FakeVectorStore:
    def __init__(self, hits):
        self._hits = hits
        self.requests = []

    def search_hybrid_tender_version(
        self, vector, query, project_id, document_version_id, limit, vector_weight=0.7
    ):
        self.requests.append((vector, query, project_id, document_version_id, limit))
        return self._hits


class FakeReranker:
    def rerank(self, query, documents):
        assert query and documents
        return [float(index) for index, _ in enumerate(documents, start=1)]


class FakeSearchRepository:
    def __init__(self, chunks):
        self._chunks = chunks
        self.requests = []

    def list_visible_project_chunks(self, project_id, chunk_pks, *, document_version_id=None):
        self.requests.append((project_id, chunk_pks, document_version_id))
        return [chunk for chunk in self._chunks if str(chunk.id) in chunk_pks]


def _chunk(index: int) -> SearchChunk:
    evidence_id = UUID(f"00000000-0000-0000-0000-0000000002{index:02d}")
    return SearchChunk(
        id=UUID(f"00000000-0000-0000-0000-0000000003{index:02d}"),
        source_document_version_id=_VERSION_ID,
        source_node_id=UUID(f"00000000-0000-0000-0000-0000000004{index:02d}"),
        evidence_id=evidence_id,
        project_id=_PROJECT_ID,
        chunk_type="TENDER",
        chunk_index=index,
        content=f"chunk {index} content",
        content_hash=f"hash-{index}",
        metadata_={"page_number": index},
        indexed_at=None,
        deleted_at=None,
    )


def test_retriever_uses_version_scoped_reranked_chunks_for_every_specialist():
    chunks = [_chunk(index) for index in range(1, 4)]
    vector_store = FakeVectorStore([VectorSearchHit(str(chunk.id)) for chunk in chunks])
    search = FakeSearchRepository(chunks)
    retriever = TenderAgentContextRetriever.__new__(TenderAgentContextRetriever)
    retriever._embedding_client = FakeEmbeddingClient()
    retriever._vector_store = vector_store
    retriever._reranker = FakeReranker()
    retriever._search = search

    result = retriever.retrieve(_PROJECT_ID, _VERSION_ID)

    assert len(vector_store.requests) == 7
    assert all(
        project_id == str(_PROJECT_ID)
        and document_version_id == str(_VERSION_ID)
        and limit == 24
        for _, _, project_id, document_version_id, limit in vector_store.requests
    )
    assert len(search.requests) == 1
    assert all(
        project_id == _PROJECT_ID and document_version_id == _VERSION_ID
        for project_id, _, document_version_id in search.requests
    )
    assert set(result.specialist_contexts) == {
        "qualification",
        "commercial",
        "technical",
        "scoring",
        "schedule",
    }
    assert all(len(contexts) == 3 for contexts in result.specialist_contexts.values())
    assert len(result.legal_context) == 3
    assert result.overview[0]["evidence_id"] == str(chunks[-1].evidence_id)
    assert result.evidence_ids == {chunk.evidence_id for chunk in chunks}




def test_retriever_splits_legacy_long_chunk_into_bounded_passages():
    chunk = _chunk(1)
    chunk.content = (
        "\u8d44\u683c\u6761\u4ef6\u3002"
        + ("A" * 1_500)
        + "\u3002\u4e1a\u7ee9\u8981\u6c42\u3002"
        + ("B" * 1_500)
    )

    passages = TenderAgentContextRetriever._passages(chunk)

    assert len(passages) == 3
    assert all(len(passage.content) <= 1_200 for passage in passages)
    assert passages[0].char_start == 0
    assert passages[-1].char_end == len(chunk.content)
    assert passages[1].char_start < passages[0].char_end
