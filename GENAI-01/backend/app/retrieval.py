"""Role-filtered Qdrant dense retrieval, BM25 lexical retrieval and RRF fusion."""

import logging
import math
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from app.models import Chunk, SearchHit
from app.tracing import traceable

logger = logging.getLogger(__name__)


class InMemoryVectorStore:
    """Small deterministic store used by unit tests and local no-infrastructure demos."""

    def __init__(self) -> None:
        self._rows: dict[str, tuple[list[float], Chunk]] = {}

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one vector")
        for chunk, vector in zip(chunks, vectors, strict=True):
            self._rows[chunk.chunk_id] = (vector, chunk)

    async def dense_search(self, vector: list[float], role: str, limit: int) -> list[SearchHit]:
        candidates: list[SearchHit] = []
        for stored_vector, chunk in self._rows.values():
            if role not in chunk.metadata.get("allowed_roles", []):
                continue
            score = _cosine(vector, stored_vector)
            normalized_score = max(0.0, min(1.0, score))
            candidates.append(SearchHit(chunk=chunk, score=normalized_score, dense_score=normalized_score))
        return sorted(candidates, key=lambda hit: hit.dense_score, reverse=True)[:limit]

    async def scroll_allowed(self, role: str, limit: int = 10000) -> list[Chunk]:
        visible = [
            chunk for _, chunk in self._rows.values()
            if role in chunk.metadata.get("allowed_roles", [])
        ]
        return visible[:limit]

    async def delete_sources(self, sources: set[str]) -> None:
        self._rows = {
            chunk_id: (vector, chunk)
            for chunk_id, (vector, chunk) in self._rows.items()
            if str(chunk.metadata.get("source", "")) not in sources
        }

    async def count(self) -> int:
        return len(self._rows)

    async def close(self) -> None:
        return None


class QdrantVectorStore:
    """Async Qdrant adapter; the role predicate is attached to every vector and scroll query."""

    def __init__(self, url: str, collection: str, vector_size: int, api_key: str | None = None) -> None:
        try:
            from qdrant_client import AsyncQdrantClient
        except ImportError as exc:
            raise RuntimeError("qdrant-client is required when VECTOR_STORE=qdrant") from exc
        self.client = AsyncQdrantClient(url=url, api_key=api_key, timeout=30)
        self.collection = collection
        self.vector_size = vector_size

    async def ensure_collection(self) -> None:
        from qdrant_client.http.models import Distance, VectorParams

        collections = await self.client.get_collections()
        existing = {item.name for item in collections.collections}
        if self.collection not in existing:
            await self.client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE),
            )
        from qdrant_client.http.models import PayloadSchemaType

        collection_info = await self.client.get_collection(self.collection)
        payload_schema = collection_info.payload_schema or {}
        for field_name in ("allowed_roles", "source"):
            if field_name not in payload_schema:
                await self.client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=PayloadSchemaType.KEYWORD,
                    wait=True,
                )

    @staticmethod
    def _role_filter(role: str) -> Any:
        from qdrant_client.http.models import FieldCondition, Filter, MatchValue

        return Filter(must=[FieldCondition(key="allowed_roles", match=MatchValue(value=role))])

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        from qdrant_client.http.models import PointStruct

        if len(chunks) != len(vectors):
            raise ValueError("Each chunk must have exactly one vector")
        points = [
            PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk.chunk_id)),
                vector=vector,
                payload={"chunk_id": chunk.chunk_id, "text": chunk.text, **chunk.metadata},
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        if points:
            await self.client.upsert(collection_name=self.collection, points=points, wait=True)

    @staticmethod
    def _chunk_from_payload(payload: dict[str, Any]) -> Chunk:
        metadata = {
            key: value for key, value in payload.items()
            if key not in {"chunk_id", "text"}
        }
        return Chunk(chunk_id=str(payload["chunk_id"]), text=str(payload["text"]), metadata=metadata)

    async def dense_search(self, vector: list[float], role: str, limit: int) -> list[SearchHit]:
        response = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=self._role_filter(role),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            SearchHit(
                chunk=self._chunk_from_payload(point.payload or {}),
                score=max(0.0, min(1.0, float(point.score))),
                dense_score=max(0.0, min(1.0, float(point.score))),
            )
            for point in response.points
        ]

    async def scroll_allowed(self, role: str, limit: int = 100000) -> list[Chunk]:
        chunks: list[Chunk] = []
        offset: Any = None
        while len(chunks) < limit:
            page, next_offset = await self.client.scroll(
                collection_name=self.collection,
                scroll_filter=self._role_filter(role),
                limit=min(1000, limit - len(chunks)),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            chunks.extend(self._chunk_from_payload(point.payload or {}) for point in page)
            if next_offset is None or not page:
                break
            offset = next_offset
        return chunks

    async def delete_sources(self, sources: set[str]) -> None:
        from qdrant_client.http.models import FieldCondition, Filter, FilterSelector, MatchValue

        for source in sorted(sources):
            await self.client.delete(
                collection_name=self.collection,
                points_selector=FilterSelector(
                    filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value=source))])
                ),
                wait=True,
            )

    async def count(self) -> int:
        result = await self.client.count(collection_name=self.collection, exact=True)
        return int(result.count)

    async def close(self) -> None:
        await self.client.close()


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.casefold())


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


def _bm25_scores(query: str, chunks: list[Chunk], k1: float = 1.5, b: float = 0.75) -> list[float]:
    """BM25 implementation kept local so offline tests do not depend on model services."""
    if not chunks:
        return []
    tokenized = [_tokens(chunk.text) for chunk in chunks]
    query_tokens = set(_tokens(query))
    document_count = len(tokenized)
    lengths = [len(document) for document in tokenized]
    average_length = sum(lengths) / max(1, document_count)
    document_frequency: Counter[str] = Counter()
    term_frequencies: list[Counter[str]] = []
    for document in tokenized:
        frequencies = Counter(document)
        term_frequencies.append(frequencies)
        document_frequency.update(frequencies.keys())
    scores: list[float] = []
    for index, frequencies in enumerate(term_frequencies):
        score = 0.0
        document_length = lengths[index]
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if frequency == 0:
                continue
            df = document_frequency[token]
            inverse_document_frequency = math.log(1.0 + (document_count - df + 0.5) / (df + 0.5))
            denominator = frequency + k1 * (1.0 - b + b * document_length / max(1.0, average_length))
            score += inverse_document_frequency * (frequency * (k1 + 1.0)) / denominator
        scores.append(score)
    return scores


class HybridRetriever:
    def __init__(
        self,
        store: InMemoryVectorStore | QdrantVectorStore,
        dense_top_k: int = 24,
        lexical_top_k: int = 24,
        rrf_k: int = 60,
    ) -> None:
        self.store = store
        self.dense_top_k = dense_top_k
        self.lexical_top_k = lexical_top_k
        self.rrf_k = rrf_k

    @traceable(name="rag_hybrid_retrieval", run_type="retriever")
    async def retrieve(
        self,
        query: str,
        query_vector: list[float],
        role: str,
        mode: str = "hybrid_rerank",
        final_top_n: int = 6,
    ) -> list[SearchHit]:
        dense = await self.store.dense_search(query_vector, role, self.dense_top_k)
        if mode == "dense":
            return dense[:final_top_n]

        # scroll_allowed applies the Qdrant payload filter before the lexical corpus is constructed.
        visible_chunks = await self.store.scroll_allowed(role)
        lexical_raw = _bm25_scores(query, visible_chunks)
        lexical_by_id = {
            chunk.chunk_id: score for chunk, score in zip(visible_chunks, lexical_raw, strict=True)
        }
        lexical_ranked = sorted(visible_chunks, key=lambda chunk: lexical_by_id[chunk.chunk_id], reverse=True)
        lexical_ranked = [chunk for chunk in lexical_ranked if lexical_by_id[chunk.chunk_id] > 0][:self.lexical_top_k]

        candidates: dict[str, SearchHit] = {hit.chunk.chunk_id: hit for hit in dense}
        dense_ranks = {hit.chunk.chunk_id: rank for rank, hit in enumerate(dense, start=1)}
        lexical_ranks = {chunk.chunk_id: rank for rank, chunk in enumerate(lexical_ranked, start=1)}
        for chunk in lexical_ranked:
            existing = candidates.get(chunk.chunk_id)
            raw = lexical_by_id[chunk.chunk_id]
            lexical_confidence = raw / (raw + 1.5)
            if existing is None:
                candidates[chunk.chunk_id] = SearchHit(
                    chunk=chunk,
                    score=lexical_confidence,
                    lexical_score=lexical_confidence,
                )
            else:
                existing.lexical_score = lexical_confidence
                existing.score = max(existing.score, lexical_confidence)

        fused: list[tuple[float, SearchHit]] = []
        for chunk_id, hit in candidates.items():
            rrf_score = 0.0
            if chunk_id in dense_ranks:
                rrf_score += 1.0 / (self.rrf_k + dense_ranks[chunk_id])
            if chunk_id in lexical_ranks:
                rrf_score += 1.0 / (self.rrf_k + lexical_ranks[chunk_id])
            hit.retrieval_rank = dense_ranks.get(chunk_id, 0) or lexical_ranks.get(chunk_id, 0)
            fused.append((rrf_score, hit))
        fused.sort(key=lambda item: (item[0], item[1].score), reverse=True)
        return [hit for _, hit in fused[:final_top_n]]


class CohereReranker:
    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._client: Any = None

    @traceable(name="cohere_rerank", run_type="reranker")
    async def rerank(self, query: str, hits: list[SearchHit], top_n: int) -> list[SearchHit]:
        if not hits or not self.api_key:
            return hits[:top_n]
        if self._client is None:
            try:
                import cohere
            except ImportError as exc:
                raise RuntimeError("Install cohere to enable Cohere reranking") from exc
            self._client = cohere.AsyncClient(api_key=self.api_key)
        response = await self._client.rerank(
            model=self.model,
            query=query,
            documents=[hit.chunk.text for hit in hits],
            top_n=min(top_n, len(hits)),
        )
        reranked: list[SearchHit] = []
        for rank, result in enumerate(response.results, start=1):
            hit = hits[int(result.index)]
            hit.rerank_score = max(0.0, min(1.0, float(result.relevance_score)))
            hit.score = hit.rerank_score
            hit.retrieval_rank = rank
            reranked.append(hit)
        return reranked
