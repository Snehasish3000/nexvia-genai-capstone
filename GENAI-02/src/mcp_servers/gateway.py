"""In-process MCP v2 client gateway shared by the autonomous agents.

MCP tools are called through the real protocol dispatcher. This avoids starting a
separate OS process for every internal tool invocation while preserving the same
schemas and tool-call boundary exposed by the stdio server entry points.
"""
import base64
import json
import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import Client

from src.config import Settings
from src.mcp_servers.filesystem_service import WorkspaceFileService
from src.mcp_servers.filesystem_server import mcp as filesystem_server
from src.mcp_servers.playwright_server import mcp as playwright_server
from src.mcp_servers.postgres_server import mcp as postgres_server
from src.mcp_servers.sec_server import mcp as sec_server
from src.mcp_servers.web_search_server import mcp as web_search_server
from src.telemetry.otel import span

logger = logging.getLogger(__name__)


class MCPToolError(RuntimeError):
    """Raised when an MCP tool returns an error or an undecodable result."""


class MCPToolGateway(WorkspaceFileService):
    """Long-lived in-process MCP clients for search, SEC, browser, files, and Postgres."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.settings = settings
        self._stack: AsyncExitStack | None = None
        self._clients: dict[str, Any] = {}
        self._servers = {
            "web_search": web_search_server,
            "sec": sec_server,
            "browser": playwright_server,
            "filesystem": filesystem_server,
            "postgres": postgres_server,
        }

    async def start(self) -> None:
        if self._stack is not None:
            return
        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            for name, server in self._servers.items():
                self._clients[name] = await stack.enter_async_context(Client(server))
        except Exception:
            await stack.aclose()
            self._clients.clear()
            raise
        self._stack = stack
        logger.info("In-process MCP clients initialized", extra={"tool": "mcp_gateway", "server_count": len(self._clients)})

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
            self._stack = None
        self._clients.clear()

    @staticmethod
    def _decode_result(result: Any) -> Any:
        if bool(getattr(result, "isError", getattr(result, "is_error", False))):
            messages = [str(getattr(item, "text", "")) for item in getattr(result, "content", []) or []]
            raise MCPToolError("MCP tool returned an error: " + "; ".join(messages)[:1000])
        structured = getattr(result, "structured_content", None)
        if structured is not None:
            if isinstance(structured, dict) and set(structured) == {"result"}:
                return structured["result"]
            return structured
        content = getattr(result, "content", []) or []
        text_blocks = [str(getattr(item, "text", "")) for item in content if getattr(item, "text", None) is not None]
        if len(text_blocks) == 1:
            try:
                return json.loads(text_blocks[0])
            except json.JSONDecodeError:
                return text_blocks[0]
        if text_blocks:
            return text_blocks
        return None

    async def _call(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        client = self._clients.get(server_name)
        if client is None:
            raise MCPToolError("MCP gateway is not started")
        with span("mcp.client.call", {"mcp.server": server_name, "mcp.tool": tool_name}):
            result = await client.call_tool(tool_name, arguments)
            return self._decode_result(result)

    async def search(self, query: str, max_results: int = 6) -> list[dict[str, Any]]:
        result = await self._call("web_search", "web_search", {"query": query, "max_results": max(1, min(int(max_results), 10))})
        if not isinstance(result, list):
            raise MCPToolError("web_search returned an unexpected payload")
        return [item for item in result if isinstance(item, dict)]

    async def research_company(self, query: str, limit: int = 4) -> dict[str, Any] | None:
        result = await self._call("sec", "sec_edgar_company_research", {"query": query, "limit": max(1, min(int(limit), 6))})
        return result if isinstance(result, dict) else None

    async def fetch_text(self, url: str) -> dict[str, Any]:
        result = await self._call("browser", "browser_fetch", {"url": url})
        if not isinstance(result, dict):
            raise MCPToolError("browser_fetch returned an unexpected payload")
        return result

    async def read_text(self, relative_path: str) -> dict[str, str | int]:
        result = await self._call("filesystem", "read_workspace_file", {"relative_path": relative_path})
        if not isinstance(result, dict):
            raise MCPToolError("read_workspace_file returned an unexpected payload")
        return result

    async def write_text(self, relative_path: str, content: str) -> dict[str, str | int]:
        result = await self._call("filesystem", "write_workspace_file", {"relative_path": relative_path, "content": content})
        if not isinstance(result, dict):
            raise MCPToolError("write_workspace_file returned an unexpected payload")
        return result

    async def write_bytes(self, relative_path: str, content: bytes) -> dict[str, str | int]:
        encoded = base64.b64encode(content).decode("ascii")
        result = await self._call("filesystem", "write_workspace_bytes", {"relative_path": relative_path, "base64_content": encoded})
        if not isinstance(result, dict):
            raise MCPToolError("write_workspace_bytes returned an unexpected payload")
        return result

    async def list_files(self) -> list[str]:
        result = await self._call("filesystem", "list_workspace_files", {})
        if not isinstance(result, list):
            raise MCPToolError("list_workspace_files returned an unexpected payload")
        return [str(item) for item in result]

    async def query_history(self, sql: str, limit: int = 100) -> list[dict[str, Any]]:
        result = await self._call("postgres", "query_run_history", {"sql": sql, "limit": max(1, min(int(limit), 250))})
        if not isinstance(result, list):
            raise MCPToolError("query_run_history returned an unexpected payload")
        return [item for item in result if isinstance(item, dict)]
