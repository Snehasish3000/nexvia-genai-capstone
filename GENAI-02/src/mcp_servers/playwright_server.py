"""MCP stdio server exposing allowlisted Playwright browser automation."""
from mcp.server import MCPServer

from src.config import get_settings
from src.mcp_servers.browser_service import BrowserService

mcp = MCPServer("nexvia-playwright-browser")
service = BrowserService(get_settings())


@mcp.tool()
async def browser_fetch(url: str) -> dict[str, object]:
    """Fetch page title and readable text from an approved HTTPS domain."""
    return await service.fetch_text(url)


if __name__ == "__main__":
    mcp.run(transport="stdio")
