"""Validated environment configuration."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env", "../../.env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "Nexvia Enterprise RAG"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    allow_demo_auth: bool = True
    jwt_secret: str = "dev-only-change-this-secret-before-deployment-32chars"
    jwt_issuer: str = "nexvia-genai-01"
    jwt_expiry_minutes: int = Field(default=480, ge=5, le=10080)
    max_query_chars: int = Field(default=2000, ge=50, le=10000)
    max_upload_bytes: int = Field(default=25 * 1024 * 1024, ge=1024)

    vector_store: Literal["qdrant", "memory"] = "qdrant"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "nexvia_enterprise_knowledge"
    vector_size: int = Field(default=384, ge=32, le=3072)
    dense_top_k: int = Field(default=24, ge=1, le=100)
    lexical_top_k: int = Field(default=24, ge=1, le=100)
    final_top_n: int = Field(default=6, ge=1, le=30)
    min_relevance_score: float = Field(default=0.24, ge=0.0, le=1.0)
    rrf_k: int = Field(default=60, ge=1, le=200)
    parent_expansion: bool = True

    embedding_provider: Literal["hash", "openai"] = "hash"
    embedding_model: str = "text-embedding-3-small"
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4o-mini"
    llm_provider: Literal["mock", "openai"] = "mock"
    llm_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    llm_max_tokens: int = Field(default=900, ge=64, le=8000)
    input_cost_per_million_tokens: float = Field(default=0.15, ge=0.0)
    output_cost_per_million_tokens: float = Field(default=0.60, ge=0.0)
    cohere_api_key: str | None = None
    cohere_rerank_model: str = "rerank-v3.5"

    sqlite_path: Path = Path("./data/nexvia_rag.sqlite3")
    cache_ttl_seconds: int = Field(default=900, ge=0, le=86400)
    semantic_cache_threshold: float = Field(default=0.985, ge=0.8, le=1.0)
    cache_enabled: bool = True

    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "nexvia-genai-01"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    @model_validator(mode="after")
    def validate_security_and_providers(self) -> "Settings":
        if self.environment == "production":
            if self.jwt_secret.startswith("dev-only-") or len(self.jwt_secret) < 32:
                raise ValueError("JWT_SECRET must be a high-entropy secret of at least 32 chars in production")
            if self.allow_demo_auth:
                raise ValueError("ALLOW_DEMO_AUTH must be false in production")
        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when EMBEDDING_PROVIDER=openai")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return self

    @property
    def allowed_cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
