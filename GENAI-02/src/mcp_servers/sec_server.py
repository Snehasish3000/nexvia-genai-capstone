"""MCP stdio server exposing SEC EDGAR issuer, filing and XBRL facts research."""
from mcp.server import MCPServer

from src.config import get_settings
from src.mcp_servers.sec_service import SECResearchService

mcp = MCPServer("nexvia-sec-edgar")
service = SECResearchService(get_settings())


@mcp.tool()
async def sec_edgar_company_research(query: str, limit: int = 4) -> dict[str, object] | None:
    """Resolve a company to SEC filings and selected annual US-GAAP XBRL facts."""
    return await service.research_company(query, limit=max(1, min(limit, 6)))


if __name__ == "__main__":
    mcp.run(transport="stdio")
