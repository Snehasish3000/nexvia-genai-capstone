"""Workspace-confined file tools; never exposes arbitrary host filesystem paths."""
from pathlib import Path

from src.config import Settings
from src.telemetry.otel import observe_tool, span


class WorkspaceFileService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.workspace_dir.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PermissionError("Path traversal outside the managed workspace is blocked")
        return candidate

    @observe_tool("mcp.filesystem.write")
    async def write_text(self, relative_path: str, content: str) -> dict[str, str | int]:
        path = self.resolve(relative_path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.settings.workspace_max_file_bytes:
            raise ValueError("File exceeds configured workspace size limit")
        with span("mcp.filesystem.write", {"mcp.tool": "write_workspace_file", "file.bytes": len(encoded)}):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return {"path": str(path.relative_to(self.root)), "bytes_written": len(encoded)}


    @observe_tool("mcp.filesystem.write_bytes")
    async def write_bytes(self, relative_path: str, content: bytes) -> dict[str, str | int]:
        path = self.resolve(relative_path)
        if len(content) > self.settings.workspace_max_file_bytes:
            raise ValueError("Binary artifact exceeds configured workspace size limit")
        with span("mcp.filesystem.write_bytes", {"mcp.tool": "write_workspace_bytes", "file.bytes": len(content)}):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return {"path": str(path.relative_to(self.root)), "bytes_written": len(content)}

    @observe_tool("mcp.filesystem.read")
    async def read_text(self, relative_path: str) -> dict[str, str | int]:
        path = self.resolve(relative_path)
        if not path.is_file():
            raise FileNotFoundError(f"Workspace file not found: {relative_path}")
        if path.stat().st_size > self.settings.workspace_max_file_bytes:
            raise ValueError("File exceeds configured workspace size limit")
        with span("mcp.filesystem.read", {"mcp.tool": "read_workspace_file"}):
            return {"path": str(path.relative_to(self.root)), "content": path.read_text(encoding="utf-8"), "bytes_read": path.stat().st_size}

    @observe_tool("mcp.filesystem.list")
    async def list_files(self) -> list[str]:
        return sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())
