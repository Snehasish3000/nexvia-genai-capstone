"""Domain models shared by ingestion, retrieval and answer orchestration."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1)
    doc_type: Literal["pdf", "docx", "html", "url", "text"]
    allowed_roles: list[str] = Field(default_factory=lambda: ["admin"])
    page: int | None = Field(default=None, ge=1)
    ingested_at: datetime
    source_uri: str | None = None


class Chunk(BaseModel):
    model_config = ConfigDict(extra="allow")

    chunk_id: str
    text: str
    metadata: dict[str, Any]


class SearchHit(BaseModel):
    chunk: Chunk
    score: float = Field(ge=0.0, le=1.0)
    dense_score: float = Field(default=0.0, ge=0.0, le=1.0)
    lexical_score: float = Field(default=0.0, ge=0.0, le=1.0)
    rerank_score: float | None = Field(default=None, ge=0.0, le=1.0)
    retrieval_rank: int = Field(default=0, ge=0)


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class GenerationResult(BaseModel):
    answer: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str
