"""RAG application service: cache, authorized retrieval, reranking, grounded generation and telemetry."""

import logging
import time
import uuid
from typing import Any

from app.core.config import Settings
from app.db import ApplicationDatabase
from app.embeddings import EmbeddingProvider
from app.generation import ABSTENTION, MockGenerator, OpenAIGenerator
from app.models import SearchHit
from app.retrieval import CohereReranker, HybridRetriever
from app.schemas import ChatResponse, Citation
from app.tracing import traceable

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingProvider,
        retriever: HybridRetriever,
        reranker: CohereReranker,
        database: ApplicationDatabase,
        generator: Any | None = None,
    ) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self.retriever = retriever
        self.reranker = reranker
        self.database = database
        if generator is not None:
            self.generator = generator
        elif settings.llm_provider == "openai":
            self.generator = OpenAIGenerator(
                api_key=str(settings.openai_api_key),
                model=settings.openai_chat_model,
                temperature=settings.llm_temperature,
                max_tokens=settings.llm_max_tokens,
                input_cost_per_million=settings.input_cost_per_million_tokens,
                output_cost_per_million=settings.output_cost_per_million_tokens,
            )
        else:
            self.generator = MockGenerator()

    @traceable(name="nexvia_rag_answer", run_type="chain")
    async def answer(
        self,
        question: str,
        user_id: str,
        role: str,
        retrieval_mode: str = "hybrid_rerank",
        top_k: int | None = None,
        request_id: str | None = None,
        query_vector_override: list[float] | None = None,
        precomputed_hits: list[SearchHit] | None = None,
    ) -> ChatResponse:
        started = time.perf_counter()
        request_id = request_id or str(uuid.uuid4())
        normalized_query = " ".join(question.casefold().split())
        query_vector = query_vector_override or await self.embeddings.embed_query(question)
        effective_top_k = top_k or self.settings.final_top_n
        cache_scope = (
            f"{retrieval_mode}|top={effective_top_k}|"
            f"embed={self.settings.embedding_provider}:{self.settings.embedding_model}:{self.settings.vector_size}|"
            f"llm={self.settings.llm_provider}:{self.settings.openai_chat_model}|"
            f"rerank={self.settings.cohere_rerank_model}"
        )
        if self.settings.cache_enabled:
            cached = self.database.cache_lookup(
                normalized_query=normalized_query,
                query_vector=query_vector,
                role=role,
                threshold=self.settings.semantic_cache_threshold,
                ttl_seconds=self.settings.cache_ttl_seconds,
                cache_scope=cache_scope,
            )
            if cached is not None:
                response = ChatResponse.model_validate(cached)
                response.request_id = request_id
                response.cache_hit = True
                response.latency_ms = round((time.perf_counter() - started) * 1000, 2)
                response.usage.prompt_tokens = 0
                response.usage.completion_tokens = 0
                response.usage.total_tokens = 0
                response.usage.estimated_cost_usd = 0.0
                self._record(response, user_id, role, retrieval_mode)
                return response

        if precomputed_hits is None:
            hits = await self.retriever.retrieve(
                query=question,
                query_vector=query_vector,
                role=role,
                mode=retrieval_mode,
                final_top_n=effective_top_k,
            )
            if retrieval_mode == "hybrid_rerank":
                hits = await self.reranker.rerank(question, hits, effective_top_k)
        else:
            hits = precomputed_hits[:effective_top_k]

        confidence = max((hit.rerank_score if hit.rerank_score is not None else hit.score for hit in hits), default=0.0)
        abstained = not hits or confidence < self.settings.min_relevance_score
        if abstained:
            answer_text = ABSTENTION
            from app.models import TokenUsage

            usage = TokenUsage()
        else:
            generation = await self.generator.generate(question, hits)
            answer_text = generation.answer
            usage = generation.usage
            abstained = answer_text == ABSTENTION
        citations = [] if abstained else [self._citation(hit) for hit in hits[: min(3, len(hits))]]
        response = ChatResponse(
            answer=answer_text,
            citations=citations,
            confidence=round(float(confidence), 4),
            abstained=abstained,
            retrieval_mode=retrieval_mode,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
            usage=usage,
            cache_hit=False,
            request_id=request_id,
        )
        if self.settings.cache_enabled and not abstained:
            self.database.cache_save(
                normalized_query=normalized_query,
                query_vector=query_vector,
                role=role,
                response=response.model_dump(mode="json"),
                cache_scope=cache_scope,
            )
        self._record(response, user_id, role, retrieval_mode)
        logger.info(
            "RAG query completed",
            extra={
                "event": "rag_query_completed", "request_id": request_id, "user_id": user_id,
                "role": role, "latency_ms": response.latency_ms,
            },
        )
        return response

    @staticmethod
    def _citation(hit: SearchHit) -> Citation:
        metadata = hit.chunk.metadata
        return Citation(
            chunk_id=hit.chunk.chunk_id,
            source=str(metadata.get("source", "unknown")),
            page=metadata.get("page"),
            quote=hit.chunk.text[:360],
            score=round(hit.rerank_score if hit.rerank_score is not None else hit.score, 4),
        )

    def _record(self, response: ChatResponse, user_id: str, role: str, retrieval_mode: str) -> None:
        self.database.record_query(
            request_id=response.request_id,
            user_id=user_id,
            role=role,
            latency_ms=response.latency_ms,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            estimated_cost_usd=response.usage.estimated_cost_usd,
            retrieval_mode=retrieval_mode,
            cache_hit=response.cache_hit,
            abstained=response.abstained,
        )
