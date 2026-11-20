from __future__ import annotations

import json

import httpx
import pytest

from api_client import ApiError, WorkflowApiClient


def test_dashboard_api_client_covers_health_runs_thread_events_and_approval() -> None:
    seen: list[tuple[str, str, dict[str, str]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, dict(request.headers)))
        if request.url.path == "/api/v1/health":
            return httpx.Response(200, json={"status": "ok", "service": "test", "database": "connected", "sandbox_mode": "mock"})
        if request.url.path == "/api/v1/runs":
            return httpx.Response(200, json=[])
        if request.url.path == "/api/v1/threads/thread-123":
            return httpx.Response(200, json={"thread_id": "thread-123", "status": "awaiting_approval"})
        if request.url.path == "/api/v1/threads/thread-123/approve":
            body = json.loads(request.content.decode("utf-8"))
            assert body["action"] == "approve"
            return httpx.Response(200, json={"thread_id": "thread-123", "status": "completed"})
        if request.url.path == "/api/v1/runs/run-123/events":
            return httpx.Response(200, json=[{"event_type": "run_started"}])
        return httpx.Response(404, json={"detail": "not found"})

    transport = httpx.MockTransport(handler)
    with WorkflowApiClient("http://testserver", "secret-test-token", transport=transport) as client:
        assert client.health()["status"] == "ok"
        assert client.list_runs() == []
        assert client.get_thread("thread-123")["status"] == "awaiting_approval"
        assert client.approve("thread-123", action="approve", note="reviewed")["status"] == "completed"
        assert client.run_events("run-123")[0]["event_type"] == "run_started"

    assert len(seen) == 5
    assert all(item[2].get("authorization") == "Bearer secret-test-token" for item in seen)


def test_dashboard_client_rejects_non_http_url() -> None:
    with pytest.raises(ValueError):
        WorkflowApiClient("localhost:8010")


def test_dashboard_client_surfaces_backend_errors() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"detail": "A valid bearer token is required"}))
    with WorkflowApiClient("http://testserver", transport=transport) as client:
        with pytest.raises(ApiError) as captured:
            client.health()
    assert captured.value.status_code == 401
    assert "valid bearer token" in str(captured.value)
