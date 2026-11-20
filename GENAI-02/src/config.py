"""Centralized, validated configuration for GENAI-02."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "GENAI-02/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,
    )

    app_name: str = "Nexvia Multi-Agent Workflow"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8010

    database_url: str = "postgresql://nexvia:nexvia_dev_password@localhost:5432/nexvia_agents"
    database_connect_timeout: float = Field(default=8.0, gt=0, le=60)
    persistence_enabled: bool = True
    langgraph_aes_key: SecretStr | None = None
    api_access_token: SecretStr | None = None

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str | None = None
    tavily_api_key: SecretStr | None = None
    sec_user_agent: str = "NexviaGenAIInternship/1.0 (educational research workflow)"
    e2b_api_key: SecretStr | None = None
    sandbox_mode: Literal["e2b", "mock"] = "mock"
    sandbox_timeout_seconds: int = Field(default=25, ge=1, le=120)
    sandbox_request_timeout_seconds: int = Field(default=40, ge=1, le=180)

    workspace_dir: Path = Path("./workspace")
    workspace_max_file_bytes: int = Field(default=2_000_000, ge=1024)
    browser_allowed_domains: list[str] = Field(
        default_factory=lambda: ["sec.gov", "www.sec.gov", "data.sec.gov", "en.wikipedia.org", "wikipedia.org"]
    )
    browser_navigation_timeout_ms: int = Field(default=12_000, ge=1000, le=60_000)

    max_steps: int = Field(default=12, ge=3, le=50)
    max_revisions: int = Field(default=2, ge=0, le=5)
    max_tokens_per_run: int = Field(default=18_000, ge=500, le=200_000)
    max_cost_usd_per_run: float = Field(default=0.75, gt=0, le=100)
    max_input_chars: int = Field(default=8_000, ge=100, le=50_000)
    max_report_chars: int = Field(default=30_000, ge=1000, le=100_000)

    otel_enabled: bool = True
    otel_service_name: str = "nexvia-genai-02"
    otel_exporter_otlp_endpoint: str = "http://localhost:4318"
    otel_exporter_otlp_headers: str | None = None

    @model_validator(mode="after")
    def production_requires_api_token(self) -> "Settings":
        if self.app_env == "production" and not self.api_access_token:
            raise ValueError("API_ACCESS_TOKEN must be configured when APP_ENV=production")
        return self

    @field_validator("browser_allowed_domains")
    @classmethod
    def normalize_domains(cls, domains: list[str]) -> list[str]:
        normalized = sorted({item.strip().lower().lstrip(".") for item in domains if item.strip()})
        if not normalized:
            raise ValueError("At least one browser allowed domain must be configured")
        return normalized

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a valid Python logging level")
        return normalized


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.workspace_dir.mkdir(parents=True, exist_ok=True)
    return settings
