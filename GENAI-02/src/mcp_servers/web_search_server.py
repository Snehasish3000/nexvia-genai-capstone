"""MCP stdio server exposing the web-search tool."""
from mcp.server import MCPServer

from src.config import get_settings
from src.mcp_servers.search_service import WebSearchService

mcp = MCPServer("nexvia-web-search")
service = WebSearchService(get_settings())


@mcp.tool()
async def web_search(query: str, max_results: int = 6) -> list[dict[str, object]]:
    """Search public web references. Results are leads, not verified facts."""
    return await service.search(query, max_results=max(1, min(max_results, 10)))


if __name__ == "__main__":
    mcp.run(transport="stdio")
