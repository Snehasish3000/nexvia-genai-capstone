import pytest

from app.embeddings import HashEmbeddingProvider
from app.retrieval import HybridRetriever


@pytest.mark.asyncio
async def test_role_filter_prevents_cross_department_retrieval(runtime):
    store, _, _, _ = runtime
    embeddings = HashEmbeddingProvider(384)
    query_vector = await embeddings.embed_query("What is the CFO spending approval threshold?")
    finance_hits = await store.dense_search(query_vector, "finance", 10)
    engineering_hits = await store.dense_search(query_vector, "engineering", 10)
    assert finance_hits
    assert all("finance" in hit.chunk.metadata["allowed_roles"] for hit in finance_hits)
    assert all("finance" not in hit.chunk.metadata["allowed_roles"] for hit in engineering_hits)
    assert not any(hit.chunk.metadata["source"] == "finance_policy.docx" for hit in engineering_hits)


@pytest.mark.asyncio
async def test_hybrid_retrieval_uses_lexical_candidates(runtime):
    store, _, _, _ = runtime
    embeddings = HashEmbeddingProvider(384)
    retriever = HybridRetriever(store, dense_top_k=1, lexical_top_k=10)
    vector = await embeddings.embed_query("TLS 1.2 network transmission")
    hits = await retriever.retrieve("TLS 1.2 network transmission", vector, "engineering", "hybrid", 10)
    assert hits
    assert any(hit.lexical_score > 0 for hit in hits)
    assert any("TLS 1.2" in hit.chunk.text for hit in hits)


@pytest.mark.asyncio
async def test_dense_retrieval_returns_only_authorized_payloads(runtime):
    store, _, _, _ = runtime
    embeddings = HashEmbeddingProvider(384)
    vector = await embeddings.embed_query("production code peer review")
    hits = await store.dense_search(vector, "engineering", 10)
    assert hits
    assert all("engineering" in hit.chunk.metadata["allowed_roles"] for hit in hits)
