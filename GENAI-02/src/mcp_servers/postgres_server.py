"""MCP stdio server exposing read-only run-history queries."""
from mcp.server import MCPServer

from src.config import get_settings
from src.mcp_servers.postgres_service import PostgresQueryService

mcp = MCPServer("nexvia-run-history-postgres")
service = PostgresQueryService(get_settings())


@mcp.tool()
async def query_run_history(sql: str, limit: int = 100) -> list[dict[str, object]]:
    """Run a read-only SELECT against agent_runs or agent_run_events."""
    return await service.query(sql, limit=limit)


if __name__ == "__main__":
    mcp.run(transport="stdio")
