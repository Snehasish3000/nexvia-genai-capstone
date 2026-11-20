"""Web-search handler shared by the researcher and the MCP server."""
import logging
from typing import Any
from urllib.parse import quote

import httpx

from src.config import Settings
from src.telemetry.otel import observe_tool, span

logger = logging.getLogger(__name__)


class WebSearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(12.0, connect=5.0)

    @observe_tool("mcp.web_search")
    async def search(self, query: str, max_results: int = 6) -> list[dict[str, Any]]:
        normalized = " ".join(query.split())[:500]
        max_results = max(1, min(int(max_results), 10))
        if not normalized:
            return []
        with span("mcp.web_search", {"mcp.tool": "web_search", "query.length": len(normalized)}):
            if self.settings.tavily_api_key:
                try:
                    return await self._tavily_search(normalized, max_results)
                except Exception as exc:
                    logger.warning("Tavily search failed; using Wikipedia fallback: %s", type(exc).__name__)
            try:
                return await self._wikipedia_search(normalized, max_results)
            except Exception as exc:
                logger.warning("Wikipedia search failed: %s", type(exc).__name__)
                return []

    async def _tavily_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        key = self.settings.tavily_api_key.get_secret_value() if self.settings.tavily_api_key else ""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": key, "query": query, "max_results": max_results, "include_answer": False},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            {
                "title": item.get("title", "Untitled source"),
                "url": item.get("url", ""),
                "source_type": "web_search",
                "excerpt": item.get("content", "")[:2000],
            }
            for item in payload.get("results", [])[:max_results]
            if item.get("url")
        ]

    async def _wikipedia_search(self, query: str, max_results: int) -> list[dict[str, Any]]:
        params = {"action": "opensearch", "search": query, "limit": str(max_results), "namespace": "0", "format": "json"}
        async with httpx.AsyncClient(timeout=self.timeout, headers={"User-Agent": "NexviaGenAIResearchDemo/1.0 (educational capstone)"}) as client:
            response = await client.get("https://en.wikipedia.org/w/api.php", params=params)
            response.raise_for_status()
            payload = response.json()
        titles = payload[1] if len(payload) > 1 else []
        descriptions = payload[2] if len(payload) > 2 else []
        urls = payload[3] if len(payload) > 3 else []
        results: list[dict[str, Any]] = []
        for index, url in enumerate(urls[:max_results]):
            if not isinstance(url, str) or not url.startswith("https://en.wikipedia.org/"):
                continue
            results.append({
                "title": str(titles[index]) if index < len(titles) else "Wikipedia result",
                "url": url,
                "source_type": "encyclopedia_reference",
                "excerpt": str(descriptions[index]) if index < len(descriptions) else "",
            })
        # SEC EDGAR search is provided as a clearly labelled primary-source lead, not a claim that a filing was read.
        if any(term in query.lower() for term in ("revenue", "financial", "filing", "10-k", "company", "stock", "market")):
            sec_query = quote(query[:120])
            results.append({
                "title": "SEC EDGAR full-text search (primary-source lead)",
                "url": f"https://www.sec.gov/edgar/search/#/q={sec_query}",
                "source_type": "primary_source_search",
                "excerpt": "Open the SEC EDGAR query to verify official filings. This search link is not itself an extracted filing.",
            })
        return results[:max_results]
