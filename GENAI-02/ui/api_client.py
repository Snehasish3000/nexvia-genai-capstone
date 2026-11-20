"""Typed-enough synchronous HTTP client used by the Streamlit operator console."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx


class ApiError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, detail: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class WorkflowApiClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        timeout_seconds: float = 25.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized = base_url.strip().rstrip("/")
        if not normalized.startswith(("http://", "https://")):
            raise ValueError("API base URL must start with http:// or https://")
        headers = {"Accept": "application/json"}
        if token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        self._client = httpx.Client(
            base_url=normalized,
            headers=headers,
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 8.0)),
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "WorkflowApiClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            response = self._client.request(method, path, json=payload)
        except httpx.HTTPError as exc:
            raise ApiError(f"Cannot reach GENAI-02 API: {type(exc).__name__}: {exc}") from exc
        try:
            data = response.json()
        except ValueError:
            data = {"detail": response.text[:1200]}
        if response.is_error:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            raise ApiError(
                f"API returned HTTP {response.status_code}: {detail}",
                status_code=response.status_code,
                detail=detail,
            )
        return data

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/health")

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        value = self._request("GET", f"/api/v1/runs?limit={max(1, min(limit, 100))}")
        return value if isinstance(value, list) else []

    def start_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        value = self._request("POST", "/api/v1/runs", payload)
        if not isinstance(value, dict):
            raise ApiError("API start-run response was not a JSON object")
        return value

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        value = self._request("GET", f"/api/v1/threads/{quote(thread_id, safe='')}")
        if not isinstance(value, dict):
            raise ApiError("API thread response was not a JSON object")
        return value

    def approve(
        self,
        thread_id: str,
        *,
        action: str,
        note: str = "",
        edited_report: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"action": action, "note": note}
        if edited_report is not None:
            payload["edited_report"] = edited_report
        value = self._request(
            "POST",
            f"/api/v1/threads/{quote(thread_id, safe='')}/approve",
            payload,
        )
        if not isinstance(value, dict):
            raise ApiError("API approval response was not a JSON object")
        return value

    def run_events(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        value = self._request(
            "GET",
            f"/api/v1/runs/{quote(run_id, safe='')}/events?limit={max(1, min(limit, 500))}",
        )
        return value if isinstance(value, list) else []
