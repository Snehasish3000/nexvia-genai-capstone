"""FastAPI routes, JWT dependencies, streaming transport and admin-only ingestion APIs."""

import asyncio
import json
import logging
import time
import uuid
from datetime import UTC, datetime

import httpx
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import APIRouter, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from app.core.config import Settings, get_settings
from app.core.guardrails import InputGuardrails, PromptInjectionDetected
from app.core.logging import configure_logging
from app.core.security import Principal, Role, create_access_token, decode_access_token
from app.db import ApplicationDatabase
from app.embeddings import build_embedding_provider
from app.ingestion import IngestionService
from app.retrieval import CohereReranker, HybridRetriever, InMemoryVectorStore, QdrantVectorStore
from app.schemas import (
    AdminStatsResponse,
    ChatRequest,
    ChatResponse,
    DemoTokenRequest,
    DemoTokenResponse,
    FeedbackRequest,
    FeedbackResponse,
    IngestResponse,
    UrlIngestRequest,
)
from app.service import RAGService

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)
QUERY_COUNTER = Counter("nexvia_rag_queries_total", "Total RAG queries", ["role", "mode", "abstained"])
QUERY_LATENCY = Histogram("nexvia_rag_latency_ms", "RAG query latency in milliseconds", ["mode"])


def _build_runtime(settings: Settings, store: Any | None = None, database: ApplicationDatabase | None = None) -> tuple[Any, ApplicationDatabase, RAGService, IngestionService]:
    active_store = store
    if active_store is None:
        if settings.vector_store == "memory":
            active_store = InMemoryVectorStore()
        else:
            active_store = QdrantVectorStore(
                url=settings.qdrant_url,
                collection=settings.qdrant_collection,
                vector_size=settings.vector_size,
                api_key=settings.qdrant_api_key,
            )
    active_database = database or ApplicationDatabase(settings.sqlite_path)
    embeddings = build_embedding_provider(settings)
    retriever = HybridRetriever(
        store=active_store,
        dense_top_k=settings.dense_top_k,
        lexical_top_k=settings.lexical_top_k,
        rrf_k=settings.rrf_k,
    )
    reranker = CohereReranker(settings.cohere_api_key, settings.cohere_rerank_model)
    rag_service = RAGService(settings, embeddings, retriever, reranker, active_database)
    ingestion_service = IngestionService(active_store, embeddings, active_database)
    return active_store, active_database, rag_service, ingestion_service


def create_app(
    settings: Settings | None = None,
    service: RAGService | None = None,
    store: Any | None = None,
    database: ApplicationDatabase | None = None,
) -> FastAPI:
    active_settings = settings or get_settings()
    configure_logging(active_settings.log_level)
    if active_settings.langsmith_tracing:
        import os

        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = active_settings.langsmith_project
        os.environ["LANGSMITH_ENDPOINT"] = active_settings.langsmith_endpoint
        if active_settings.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = active_settings.langsmith_api_key

    built_store, built_database, built_service, ingestion_service = _build_runtime(
        active_settings, store=store, database=database
    )
    active_service = service or built_service
    guardrails = InputGuardrails(active_settings.max_query_chars)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if isinstance(built_store, QdrantVectorStore):
            last_error: Exception | None = None
            for attempt in range(1, 21):
                try:
                    await built_store.ensure_collection()
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(
                        "Waiting for Qdrant",
                        extra={"event": "qdrant_startup_retry", "attempt": attempt},
                    )
                    await asyncio.sleep(min(3.0, 0.5 * attempt))
            if last_error is not None:
                raise RuntimeError("Qdrant did not become available during startup") from last_error
        yield
        await built_store.close()

    application = FastAPI(
        title=active_settings.app_name,
        version="1.0.0",
        description=(
            "Role-filtered enterprise RAG with Qdrant dense retrieval, BM25/RRF hybrid search, "
            "Cohere reranking, grounded citations, abstention and evaluation telemetry."
        ),
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.allowed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )
    application.state.settings = active_settings
    application.state.store = built_store
    application.state.database = built_database
    application.state.rag_service = active_service
    application.state.ingestion_service = ingestion_service
    application.state.guardrails = guardrails

    @application.middleware("http")
    async def request_context(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-MS"] = f"{(time.perf_counter() - started) * 1000:.2f}"
        return response

    async def current_principal(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = Depends(security),
    ) -> Principal:
        if credentials is None or credentials.scheme.casefold() != "bearer":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
        try:
            return decode_access_token(
                credentials.credentials,
                request.app.state.settings.jwt_secret,
                request.app.state.settings.jwt_issuer,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from exc

    async def admin_principal(principal: Principal = Depends(current_principal)) -> Principal:
        if principal.role != Role.ADMIN:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
        return principal

    router = APIRouter(prefix=active_settings.api_prefix)

    @router.post("/auth/demo-token", response_model=DemoTokenResponse, tags=["authentication"])
    async def demo_token(payload: DemoTokenRequest) -> DemoTokenResponse:
        if active_settings.environment == "production" or not active_settings.allow_demo_auth:
            raise HTTPException(status_code=404, detail="Demo authentication is disabled")
        token = create_access_token(
            subject=payload.username,
            role=payload.role,
            secret=active_settings.jwt_secret,
            issuer=active_settings.jwt_issuer,
            expiry_minutes=active_settings.jwt_expiry_minutes,
        )
        return DemoTokenResponse(
            access_token=token,
            expires_in=active_settings.jwt_expiry_minutes * 60,
            role=payload.role,
        )

    async def run_chat(payload: ChatRequest, principal: Principal, request: Request) -> ChatResponse:
        try:
            inspected = guardrails.inspect(payload.question)
        except PromptInjectionDetected as exc:
            logger.warning("Prompt injection blocked", extra={"event": "prompt_injection_blocked", "role": principal.role.value})
            raise HTTPException(status_code=422, detail="Request rejected by prompt-injection guardrail") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = await active_service.answer(
            question=inspected.safe_text,
            user_id=principal.subject,
            role=principal.role.value,
            retrieval_mode=payload.retrieval_mode,
            top_k=payload.top_k,
            request_id=request.state.request_id,
        )
        QUERY_COUNTER.labels(principal.role.value, payload.retrieval_mode, str(response.abstained).lower()).inc()
        QUERY_LATENCY.labels(payload.retrieval_mode).observe(response.latency_ms)
        return response

    @router.post("/chat", response_model=ChatResponse, tags=["knowledge"])
    async def chat(
        payload: ChatRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> ChatResponse:
        return await run_chat(payload, principal, request)

    @router.post("/chat/stream", tags=["knowledge"])
    async def chat_stream(
        payload: ChatRequest,
        request: Request,
        principal: Principal = Depends(current_principal),
    ) -> StreamingResponse:
        result = await run_chat(payload, principal, request)

        async def events() -> AsyncIterator[str]:
            yield f"event: metadata\ndata: {json.dumps({'request_id': result.request_id, 'confidence': result.confidence})}\n\n"
            words = result.answer.split(" ")
            for word in words:
                yield f"event: token\ndata: {json.dumps({'text': word + ' '})}\n\n"
                await asyncio.sleep(0)
            yield f"event: complete\ndata: {result.model_dump_json()}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @router.post("/feedback", response_model=FeedbackResponse, tags=["feedback"])
    async def feedback(
        payload: FeedbackRequest,
        principal: Principal = Depends(current_principal),
    ) -> FeedbackResponse:
        comment = payload.comment
        if comment:
            try:
                comment = guardrails.inspect(comment).safe_text
            except ValueError:
                comment = "[COMMENT_REDACTED_BY_GUARDRAIL]"
        built_database.save_feedback(
            request_id=payload.request_id,
            user_id=principal.subject,
            role=principal.role.value,
            rating=payload.rating,
            comment=comment,
        )
        return FeedbackResponse()

    @router.post("/admin/documents/upload", response_model=IngestResponse, tags=["administration"])
    async def upload_document(
        file: UploadFile = File(...),
        allowed_roles: str = Form("finance,engineering,guest"),
        principal: Principal = Depends(admin_principal),
    ) -> IngestResponse:
        content = await file.read(active_settings.max_upload_bytes + 1)
        if len(content) > active_settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail="Upload exceeds configured file size limit")
        role_values = _parse_roles(allowed_roles)
        try:
            documents = ingestion_service.parser.parse_bytes(file.filename or "upload.bin", content, role_values)
            document_id, chunk_count, source = await ingestion_service.ingest_sources(documents)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        built_database.save_document(document_id, source, documents[0].doc_type, role_values, chunk_count)
        return IngestResponse(
            document_id=document_id,
            source=source,
            chunks_created=chunk_count,
            allowed_roles=[Role(role) for role in role_values],
            ingested_at=documents[0].ingested_at,
        )

    @router.post("/admin/documents/url", response_model=IngestResponse, tags=["administration"])
    async def ingest_url(
        payload: UrlIngestRequest,
        principal: Principal = Depends(admin_principal),
    ) -> IngestResponse:
        role_values = _parse_roles(",".join(role.value for role in payload.allowed_roles))
        try:
            document = await ingestion_service.parser.parse_url(str(payload.url), role_values)
            document_id, chunk_count, source = await ingestion_service.ingest_sources([document])
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=422, detail=f"URL ingestion failed: {exc}") from exc
        built_database.save_document(document_id, source, document.doc_type, role_values, chunk_count)
        return IngestResponse(
            document_id=document_id,
            source=source,
            chunks_created=chunk_count,
            allowed_roles=[Role(role) for role in role_values],
            ingested_at=document.ingested_at,
        )

    @router.get("/admin/stats", response_model=AdminStatsResponse, tags=["administration"])
    async def admin_stats(principal: Principal = Depends(admin_principal)) -> AdminStatsResponse:
        summary = built_database.metrics_summary()
        return AdminStatsResponse(
            documents=built_database.document_count(),
            chunks=await built_store.count(),
            feedback_count=int(summary["feedback_count"]),
            total_queries=int(summary["total_queries"]),
            avg_latency_ms=float(summary["avg_latency_ms"]),
            total_estimated_cost_usd=float(summary["total_estimated_cost_usd"]),
            cache_entries=built_database.cache_count(),
            generated_at=datetime.now(UTC),
        )

    application.include_router(router)

    @application.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        try:
            await built_store.count()
            return {"status": "ok", "vector_store": active_settings.vector_store}
        except Exception as exc:
            logger.exception("Health check failed")
            raise HTTPException(status_code=503, detail="Vector store unavailable") from exc

    @application.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return application


def _parse_roles(raw_roles: str) -> list[str]:
    try:
        roles = sorted({Role(item.strip().casefold()).value for item in raw_roles.split(",") if item.strip()})
    except ValueError as exc:
        allowed = ", ".join(role.value for role in Role)
        raise HTTPException(status_code=422, detail=f"allowed_roles must be selected from: {allowed}") from exc
    if not roles:
        raise HTTPException(status_code=422, detail="At least one allowed role must be selected")
    if Role.ADMIN.value not in roles:
        roles.append(Role.ADMIN.value)
        roles.sort()
    return roles

