"""MCP stdio server exposing workspace-confined file operations."""
import base64
import binascii

from mcp.server import MCPServer

from src.config import get_settings
from src.mcp_servers.filesystem_service import WorkspaceFileService

mcp = MCPServer("nexvia-workspace-files")
service = WorkspaceFileService(get_settings())


@mcp.tool()
async def read_workspace_file(relative_path: str) -> dict[str, str | int]:
    """Read a UTF-8 file inside the managed workflow workspace."""
    return await service.read_text(relative_path)


@mcp.tool()
async def write_workspace_file(relative_path: str, content: str) -> dict[str, str | int]:
    """Write a UTF-8 file inside the managed workflow workspace."""
    return await service.write_text(relative_path, content)


@mcp.tool()
async def write_workspace_bytes(relative_path: str, base64_content: str) -> dict[str, str | int]:
    """Write a base64-encoded binary artifact inside the managed workspace."""
    try:
        content = base64.b64decode(base64_content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("base64_content must be valid base64") from exc
    return await service.write_bytes(relative_path, content)


@mcp.tool()
async def list_workspace_files() -> list[str]:
    """List files currently stored in the managed workflow workspace."""
    return await service.list_files()


if __name__ == "__main__":
    mcp.run(transport="stdio")
