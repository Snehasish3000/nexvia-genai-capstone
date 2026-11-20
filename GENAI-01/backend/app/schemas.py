"""Public HTTP request and response contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

from app.core.security import Role
from app.models import TokenUsage


class DemoTokenRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[a-zA-Z0-9_.-]+$")
    role: Role


class DemoTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    role: Role


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=10000)
    retrieval_mode: Literal["dense", "hybrid", "hybrid_rerank"] = "hybrid_rerank"
    top_k: int = Field(default=6, ge=1, le=20)
    conversation_id: str | None = Field(default=None, max_length=120)


class Citation(BaseModel):
    chunk_id: str
    source: str
    page: int | None = None
    quote: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation]
    confidence: float
    abstained: bool
    retrieval_mode: str
    latency_ms: float
    usage: TokenUsage
    cache_hit: bool = False
    request_id: str


class FeedbackRequest(BaseModel):
    request_id: str = Field(min_length=5, max_length=100)
    rating: Literal["up", "down"]
    comment: str | None = Field(default=None, max_length=2000)


class FeedbackResponse(BaseModel):
    status: Literal["recorded"] = "recorded"


class IngestResponse(BaseModel):
    document_id: str
    source: str
    chunks_created: int
    allowed_roles: list[Role]
    ingested_at: datetime


class UrlIngestRequest(BaseModel):
    url: HttpUrl
    allowed_roles: list[Role] = Field(default_factory=lambda: [Role.ADMIN, Role.ENGINEERING])


class AdminStatsResponse(BaseModel):
    documents: int
    chunks: int
    feedback_count: int
    total_queries: int
    avg_latency_ms: float
    total_estimated_cost_usd: float
    cache_entries: int
    generated_at: datetime
