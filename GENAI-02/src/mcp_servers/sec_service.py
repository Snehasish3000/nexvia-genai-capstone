"""Public SEC EDGAR submissions and XBRL company-facts retrieval."""
import logging
import re
from typing import Any

import httpx

from src.config import Settings
from src.telemetry.otel import observe_tool

logger = logging.getLogger(__name__)


class SECResearchService:
    """Resolves an issuer and returns current 10-K leads plus selected annual XBRL facts."""

    TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
    USER_AGENT = "NexviaGenAIInternship/1.0 (educational research workflow)"
    CONCEPTS: dict[str, tuple[str, ...]] = {
        "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
        "net_income": ("NetIncomeLoss",),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    }
    STOPWORDS = {"the", "inc", "incorporated", "corp", "corporation", "company", "co", "ltd", "limited", "plc", "annual", "report", "financial", "analysis", "research", "latest", "filing", "filings", "10k", "10", "k"}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(15.0, connect=6.0)

    @observe_tool("mcp.sec_edgar.research")
    async def research_company(self, query: str, limit: int = 4) -> dict[str, Any] | None:
        query_tokens = {item for item in re.findall(r"[a-z0-9]+", query.lower()) if len(item) > 1 and item not in self.STOPWORDS}
        if not query_tokens:
            return None
        headers = {
            "User-Agent": self.settings.sec_user_agent or self.USER_AGENT,
            "Accept-Encoding": "gzip, deflate",
            "Accept": "application/json,text/html,*/*",
        }
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers, follow_redirects=True) as client:
            try:
                ticker_response = await client.get(self.TICKER_URL)
                ticker_response.raise_for_status()
                ticker_payload = ticker_response.json()
                match = self._match_company(ticker_payload, query_tokens)
                if match is None:
                    return None
                cik = int(match["cik_str"])
                cik_padded = f"{cik:010d}"
                submissions_response = await client.get(f"https://data.sec.gov/submissions/CIK{cik_padded}.json")
                submissions_response.raise_for_status()
                submissions = submissions_response.json()
                company_name = str(submissions.get("name") or match.get("title") or "SEC registrant")
                ticker = str(match.get("ticker") or "")
                filings = self._filing_sources(cik, submissions, limit)
                facts_payload: dict[str, Any] = {}
                try:
                    facts_response = await client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json")
                    facts_response.raise_for_status()
                    facts_payload = facts_response.json()
                except httpx.HTTPError as exc:
                    logger.warning("SEC XBRL facts request failed for CIK %s: %s", cik_padded, type(exc).__name__)
                facts = self._extract_annual_facts(facts_payload)
                fact_summary = self._format_facts(facts)
                sources: list[dict[str, Any]] = [
                    {
                        "title": f"SEC issuer submissions — {company_name} ({ticker})",
                        "url": f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
                        "source_type": "sec_edgar_submissions",
                        "excerpt": f"Official SEC registrant metadata. CIK: {cik_padded}; ticker: {ticker or 'not listed'}.",
                    }
                ]
                sources.extend(filings)
                sources.append({
                    "title": f"SEC XBRL Company Facts — {company_name}",
                    "url": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik_padded}.json",
                    "source_type": "sec_xbrl_facts",
                    "excerpt": fact_summary or "Issuer was resolved, but the selected US-GAAP annual facts were not present in the companyfacts response.",
                    "financial_facts": facts,
                })
                return {"company_name": company_name, "ticker": ticker, "cik": cik_padded, "sources": sources, "financial_facts": facts}
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                logger.warning("SEC issuer resolution/research failed: %s", type(exc).__name__)
                return None

    @classmethod
    def _match_company(cls, payload: Any, query_tokens: set[str]) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        scored: list[tuple[int, dict[str, Any]]] = []
        for item in payload.values():
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).lower()
            ticker = str(item.get("ticker", "")).lower()
            title_tokens = {token for token in re.findall(r"[a-z0-9]+", title) if len(token) > 1 and token not in cls.STOPWORDS}
            ticker_score = 100 if ticker and ticker in query_tokens else 0
            overlap = len(title_tokens & query_tokens)
            coverage = overlap / max(len(title_tokens), 1)
            score = ticker_score + overlap * 5 + (10 if coverage >= 0.75 and overlap >= 1 else 0)
            if score:
                scored.append((score, item))
        if not scored:
            return None
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if len(scored) > 1 and scored[0][0] == scored[1][0]:
            return None
        return scored[0][1]

    @staticmethod
    def _filing_sources(cik: int, submissions: dict[str, Any], limit: int) -> list[dict[str, Any]]:
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        accessions = recent.get("accessionNumber", [])
        documents = recent.get("primaryDocument", [])
        dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        selected: list[dict[str, Any]] = []
        for index, form in enumerate(forms):
            if form not in {"10-K", "10-Q", "20-F", "40-F"}:
                continue
            if index >= len(accessions) or index >= len(documents):
                continue
            accession = str(accessions[index])
            primary_document = str(documents[index])
            if not primary_document or "/" in primary_document or ".." in primary_document:
                continue
            accession_compact = accession.replace("-", "")
            filing_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accession_compact}/{primary_document}"
            filed = str(dates[index]) if index < len(dates) else "unknown filing date"
            report_date = str(report_dates[index]) if index < len(report_dates) else "period not stated"
            selected.append({
                "title": f"SEC {form} filing — filed {filed}, reporting period {report_date}",
                "url": filing_url,
                "source_type": "sec_edgar_filing",
                "excerpt": f"Official SEC filing metadata: form={form}; filed={filed}; report_date={report_date}. Values must be verified in the filing itself.",
            })
            if len(selected) >= max(1, min(limit, 6)):
                break
        return selected

    @classmethod
    def _extract_annual_facts(cls, payload: dict[str, Any]) -> list[dict[str, Any]]:
        us_gaap = payload.get("facts", {}).get("us-gaap", {})
        facts: list[dict[str, Any]] = []
        for label, candidates in cls.CONCEPTS.items():
            for concept_name in candidates:
                concept = us_gaap.get(concept_name)
                if not isinstance(concept, dict):
                    continue
                units = concept.get("units", {})
                selected_unit = next((unit for unit in ("USD", "shares", "USD/shares") if unit in units), None)
                if not selected_unit:
                    continue
                records = [item for item in units[selected_unit] if item.get("form") in {"10-K", "20-F", "40-F"} and item.get("fy")]
                by_year: dict[int, dict[str, Any]] = {}
                for item in records:
                    year = int(item["fy"])
                    current = by_year.get(year)
                    if current is None or str(item.get("filed", "")) > str(current.get("filed", "")):
                        by_year[year] = item
                for year, item in sorted(by_year.items())[-3:]:
                    facts.append({
                        "metric": label,
                        "concept": concept_name,
                        "fiscal_year": year,
                        "period_end": str(item.get("end", "")),
                        "filed": str(item.get("filed", "")),
                        "form": str(item.get("form", "")),
                        "value": float(item.get("val", 0)),
                        "unit": selected_unit,
                    })
                break
        facts.sort(key=lambda item: (item["metric"], item["fiscal_year"]))
        return facts

    @staticmethod
    def _format_facts(facts: list[dict[str, Any]]) -> str:
        if not facts:
            return ""
        lines = ["Selected SEC-reported annual US-GAAP facts (latest filing per fiscal year):"]
        for item in facts:
            value = item["value"]
            display = f"{value:,.0f}" if abs(value) >= 1 else str(value)
            lines.append(
                f"- {item['metric']} ({item['concept']}): FY{item['fiscal_year']} = {display} {item['unit']}; "
                f"period end {item['period_end']}; filed {item['filed']} ({item['form']})."
            )
        return "\n".join(lines)
