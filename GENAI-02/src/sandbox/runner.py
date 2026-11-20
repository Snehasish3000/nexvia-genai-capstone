"""Isolated Python execution through E2B; offline mode is a non-executing simulator."""
import asyncio
import base64
import logging
from typing import Any

from pydantic import BaseModel, Field

from src.config import Settings
from src.security.code_guard import UnsafeCodeError, validate_generated_python
from src.telemetry.otel import record_sandbox, span

logger = logging.getLogger(__name__)


class SandboxResult(BaseModel):
    success: bool
    mode: str
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    execution_ms: float = 0
    artifacts: list[str] = Field(default_factory=list)
    artifact_data: dict[str, str] = Field(default_factory=dict)


class SandboxRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def execute(self, code: str) -> SandboxResult:
        if not code.strip():
            return SandboxResult(success=False, mode=self.settings.sandbox_mode, error="Empty code was rejected")
        if len(code) > 20_000:
            return SandboxResult(success=False, mode=self.settings.sandbox_mode, error="Code payload exceeds 20,000 characters")
        try:
            validate_generated_python(code)
        except UnsafeCodeError as exc:
            return SandboxResult(success=False, mode=self.settings.sandbox_mode, error=f"Code policy violation: {exc}")
        with span("sandbox.execute", {"sandbox.mode": self.settings.sandbox_mode, "sandbox.code_chars": len(code)}):
            started = asyncio.get_running_loop().time()
            if self.settings.sandbox_mode == "mock":
                result = await self._simulate(code)
            elif not self.settings.e2b_api_key:
                result = SandboxResult(success=False, mode="e2b", error="E2B_API_KEY is required when SANDBOX_MODE=e2b")
            else:
                result = await self._execute_e2b(code)
            elapsed = (asyncio.get_running_loop().time() - started) * 1000
            record_sandbox(elapsed, result.success, result.mode)
            return result

    async def _simulate(self, code: str) -> SandboxResult:
        """Return deterministic fixture output without executing generated code on the host."""
        await asyncio.sleep(0)
        if not code.lstrip().startswith(("import ", "from ", "#", "print(", "goal =")):
            return SandboxResult(success=False, mode="mock", stderr="Mock sandbox rejected a script without a recognized Python entry point", error="Script rejected")
        return SandboxResult(
            success=True,
            mode="mock",
            stdout='{"analysis":"Evidence inventory completed; no unsupported financial values inferred.","data_quality":"qualitative_only","limitations":["Live normalized financial data is required for ratio analysis."]}',
            execution_ms=1.0,
            artifacts=[],
        )

    async def _execute_e2b(self, code: str) -> SandboxResult:
        try:
            from e2b_code_interpreter import AsyncSandbox
        except ImportError as exc:
            return SandboxResult(success=False, mode="e2b", error=f"E2B SDK is not installed: {exc}")

        sandbox: Any = None
        started = asyncio.get_running_loop().time()
        try:
            sandbox = await AsyncSandbox.create(api_key=self.settings.e2b_api_key.get_secret_value())
            execution = await asyncio.wait_for(
                sandbox.run_code(
                    code,
                    timeout=float(self.settings.sandbox_timeout_seconds),
                    request_timeout=float(self.settings.sandbox_request_timeout_seconds),
                ),
                timeout=float(self.settings.sandbox_request_timeout_seconds),
            )
            elapsed_ms = (asyncio.get_running_loop().time() - started) * 1000
            logs = getattr(execution, "logs", None)
            stdout = "\n".join(str(item) for item in getattr(logs, "stdout", []) or [])
            stderr = "\n".join(str(item) for item in getattr(logs, "stderr", []) or [])
            error_obj = getattr(execution, "error", None)
            artifacts: list[str] = []
            artifact_data: dict[str, str] = {}
            for index, result in enumerate(getattr(execution, "results", []) or []):
                png = getattr(result, "png", None)
                if png:
                    name = f"chart_{index + 1}.png"
                    artifacts.append(name)
                    artifact_data[name] = str(png)
                svg = getattr(result, "svg", None)
                if svg:
                    name = f"chart_{index + 1}.svg"
                    artifacts.append(name)
                    artifact_data[name] = str(svg)
                text_value = getattr(result, "text", None)
                if text_value:
                    stdout = (stdout + "\n" + str(text_value)).strip()
            return SandboxResult(
                success=error_obj is None,
                mode="e2b",
                stdout=stdout[:20_000],
                stderr=stderr[:10_000],
                error=str(error_obj)[:2000] if error_obj else None,
                execution_ms=round(elapsed_ms, 2),
                artifacts=artifacts,
                artifact_data=artifact_data,
            )
        except asyncio.TimeoutError:
            return SandboxResult(success=False, mode="e2b", error="Sandbox execution exceeded the configured request timeout", execution_ms=round((asyncio.get_running_loop().time() - started) * 1000, 2))
        except Exception as exc:
            logger.exception("E2B execution failed")
            return SandboxResult(success=False, mode="e2b", error=f"{type(exc).__name__}: {str(exc)[:1000]}", execution_ms=round((asyncio.get_running_loop().time() - started) * 1000, 2))
        finally:
            if sandbox is not None:
                try:
                    await sandbox.kill()
                except Exception as exc:
                    logger.warning("Sandbox teardown failed: %s", type(exc).__name__)
