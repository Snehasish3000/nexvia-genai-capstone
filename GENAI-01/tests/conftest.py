from datetime import UTC, datetime
from pathlib import Path
import sys

BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_PATH))

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.db import ApplicationDatabase
from app.embeddings import HashEmbeddingProvider
from app.models import SourceDocument
from app.retrieval import CohereReranker, HybridRetriever, InMemoryVectorStore
from app.service import RAGService
from app.api import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        environment="test",
        allow_demo_auth=True,
        vector_store="memory",
        embedding_provider="hash",
        llm_provider="mock",
        sqlite_path=tmp_path / "test.sqlite3",
        jwt_secret="test-secret-with-at-least-thirty-two-characters",
        min_relevance_score=0.20,
        cache_enabled=True,
        semantic_cache_threshold=0.99,
    )


@pytest.fixture
async def runtime(settings):
    store = InMemoryVectorStore()
    database = ApplicationDatabase(settings.sqlite_path)
    embeddings = HashEmbeddingProvider(settings.vector_size)
    retriever = HybridRetriever(store, dense_top_k=20, lexical_top_k=20)
    reranker = CohereReranker(api_key=None, model=settings.cohere_rerank_model)
    service = RAGService(settings, embeddings, retriever, reranker, database)
    docs = [
        SourceDocument(
            source="finance_policy.docx",
            text=(
                "A cost centre manager may approve an individual purchase up to and including USD 5,000. "
                "The Chief Financial Officer must approve purchases above USD 25,000. "
                "Finance and tax records must be retained for seven years."
            ),
            doc_type="docx",
            allowed_roles=["admin", "finance"],
            ingested_at=datetime.now(UTC),
        ),
        SourceDocument(
            source="engineering_policy.docx",
            text=(
                "Production changes require peer review by at least one qualified engineer. "
                "Automated CI tests must pass before production deployment. "
                "Data sent over a network must use TLS 1.2 or later."
            ),
            doc_type="docx",
            allowed_roles=["admin", "engineering"],
            ingested_at=datetime.now(UTC),
        ),
    ]
    from app.ingestion import IngestionService

    ingestion = IngestionService(store, embeddings, database)
    for document in docs:
        document_id, chunk_count, source = await ingestion.ingest_sources([document])
        database.save_document(document_id, source, document.doc_type, document.allowed_roles, chunk_count)
    return store, database, service, ingestion


@pytest.fixture
def client(settings, runtime):
    store, database, service, _ = runtime
    app = create_app(settings=settings, service=service, store=store, database=database)
    with TestClient(app) as test_client:
        yield test_client


def issue_token(client: TestClient, username: str, role: str) -> str:
    response = client.post("/api/v1/auth/demo-token", json={"username": username, "role": role})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]
