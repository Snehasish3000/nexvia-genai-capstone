"""Optional OpenAI-compatible LLM adapter with deterministic offline behaviour."""
import json
import logging
import re
from typing import Any

from src.config import Settings
from src.telemetry.otel import span

logger = logging.getLogger(__name__)


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: Any = None
        self.last_usage: dict[str, float | int] = {"tokens": 0, "cost_usd": 0.0}
        if settings.openai_api_key:
            try:
                from langchain_openai import ChatOpenAI

                self._client = ChatOpenAI(
                    model=settings.openai_model,
                    api_key=settings.openai_api_key.get_secret_value(),
                    base_url=settings.openai_base_url,
                    temperature=0.1,
                    timeout=45,
                    max_retries=2,
                )
            except ImportError as exc:
                logger.warning("langchain-openai unavailable; using deterministic mode: %s", exc)

    @property
    def live(self) -> bool:
        return self._client is not None

    async def complete(self, system: str, user: str, *, max_tokens: int = 1800) -> str:
        if self._client is None:
            self.last_usage = {"tokens": 0, "cost_usd": 0.0}
            return ""
        with span("llm.complete", {"llm.model": self.settings.openai_model}):
            response = await self._client.ainvoke(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                config={"metadata": {"component": "genai02"}},
                max_tokens=max_tokens,
            )
        content = response.content
        if isinstance(content, list):
            content = "\n".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        text = str(content)
        usage = getattr(response, "usage_metadata", None) or {}
        tokens = int(usage.get("total_tokens", 0)) if isinstance(usage, dict) else 0
        if not tokens:
            tokens = max(1, (len(system) + len(user) + len(text)) // 4)
        # A deliberately conservative estimate; actual provider billing remains authoritative.
        self.last_usage = {"tokens": tokens, "cost_usd": round(tokens / 1_000_000 * 2.0, 6)}
        return text

    async def complete_json(self, system: str, user: str, *, max_tokens: int = 1800) -> dict[str, Any] | None:
        content = await self.complete(system, user, max_tokens=max_tokens)
        if not content:
            return None
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I)
        try:
            value = json.loads(cleaned)
            return value if isinstance(value, dict) else None
        except json.JSONDecodeError:
            left = cleaned.find("{")
            right = cleaned.rfind("}")
            if left >= 0 and right > left:
                try:
                    value = json.loads(cleaned[left : right + 1])
                    return value if isinstance(value, dict) else None
                except json.JSONDecodeError:
                    logger.warning("LLM response was not valid JSON")
            return None
