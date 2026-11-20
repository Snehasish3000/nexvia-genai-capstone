"""Research agent calling the shared MCP tool handlers."""
from typing import Any, Protocol

from src.config import Settings
from src.schemas import ResearchSource
from src.telemetry.otel import span


class SearchTool(Protocol):
    async def search(self, query: str, max_results: int = 6) -> list[dict[str, Any]]: ...

    async def research_company(self, query: str, limit: int = 4) -> dict[str, Any] | None: ...


class BrowserTool(Protocol):
    async def fetch_text(self, url: str) -> dict[str, Any]: ...


class ResearcherAgent:
    def __init__(self, settings: Settings, search: SearchTool, browser: BrowserTool) -> None:
        self.settings = settings
        self.search = search
        self.browser = browser

    async def run(self, goal: str, company: str | None = None) -> dict[str, Any]:
        with span("agent.researcher", {"agent.name": "researcher"}):
            query = f"{company or ''} {goal}".strip()
            search_results = await self.search.search(query, max_results=8)
            sec_research = await self.search.research_company(query, limit=4)
            if sec_research and isinstance(sec_research.get("sources"), list):
                sec_sources = [source for source in sec_research["sources"] if isinstance(source, dict)]
                indexed_urls = {item.get("url") for item in search_results if isinstance(item, dict)}
                sec_sources = [source for source in sec_sources if source.get("url") not in indexed_urls]
                search_results = sec_sources + search_results
            financial_facts: list[dict[str, Any]] = []
            for item in search_results:
                if item.get("source_type") == "sec_xbrl_facts":
                    candidate_facts = item.get("financial_facts", [])
                    if isinstance(candidate_facts, list):
                        financial_facts = [fact for fact in candidate_facts if isinstance(fact, dict)]
                    break
            sources: list[dict[str, Any]] = []
            for item in search_results:
                try:
                    sources.append(ResearchSource.model_validate(item).model_dump(mode="json"))
                except Exception:
                    continue
            # Fetch a bounded number of primary-source pages; failures are recorded, not fatal.
            page_texts: list[dict[str, str]] = []
            for item in sources[:3]:
                url = item.get("url", "")
                if url:
                    try:
                        page = await self.browser.fetch_text(url)
                        page_texts.append({"url": url, "title": page.get("title", item.get("title", "")), "text": page.get("text", "")[:6000]})
                    except Exception as exc:
                        page_texts.append({"url": url, "title": item.get("title", ""), "text": f"Page fetch unavailable: {type(exc).__name__}"})
            evidence = "\n\n".join(
                f"SOURCE: {entry['url']}\nTITLE: {entry['title']}\nEXCERPT: {entry['text']}"
                for entry in page_texts
            )
            if not evidence:
                evidence = "No live page content was retrieved. Use the source register below and clearly mark evidence limitations."
            return {
                "sources": sources,
                "page_evidence": page_texts,
                "research_summary": evidence[:18_000],
                "research_findings": [],
                "financial_facts": financial_facts,
            }
