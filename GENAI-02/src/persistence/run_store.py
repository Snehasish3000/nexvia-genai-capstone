"""PostgreSQL operational run history alongside LangGraph's checkpoint tables."""
import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)


class RunStore:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._pool: Any = None

    async def connect(self) -> bool:
        if not self.settings.persistence_enabled:
            return False
        try:
            import asyncpg

            self._pool = await asyncpg.create_pool(
                self.settings.database_url,
                min_size=1,
                max_size=6,
                timeout=self.settings.database_connect_timeout,
                command_timeout=10,
            )
            schema_statements = [
                "CREATE TABLE IF NOT EXISTS agent_runs (run_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, status TEXT NOT NULL, goal TEXT NOT NULL, started_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ, total_steps INTEGER NOT NULL DEFAULT 0, token_estimate INTEGER NOT NULL DEFAULT 0, cost_usd_estimate DOUBLE PRECISION NOT NULL DEFAULT 0, metadata JSONB NOT NULL DEFAULT '{}'::jsonb)",
                "CREATE INDEX IF NOT EXISTS idx_agent_runs_started_at ON agent_runs(started_at DESC)",
                "CREATE TABLE IF NOT EXISTS agent_run_events (event_id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE, event_type TEXT NOT NULL, event_payload JSONB NOT NULL DEFAULT '{}'::jsonb, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
                "CREATE INDEX IF NOT EXISTS idx_agent_run_events_run_id ON agent_run_events(run_id, created_at)",
            ]
            async with self._pool.acquire() as connection:
                async with connection.transaction():
                    for statement in schema_statements:
                        await connection.execute(statement)
            return True
        except Exception as exc:
            logger.warning("Operational run store unavailable; checkpoint state remains the graph source of truth: %s", type(exc).__name__)
            if self._pool is not None:
                await self._pool.close()
                self._pool = None
            return False

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_run(self, run_id: str, thread_id: str, goal: str, metadata: dict[str, Any] | None = None) -> None:
        if self._pool is None:
            return
        await self._pool.execute(
            "INSERT INTO agent_runs(run_id, thread_id, status, goal, started_at, metadata) VALUES($1,$2,$3,$4,$5,$6::jsonb) ON CONFLICT(run_id) DO NOTHING",
            run_id, thread_id, "running", goal, datetime.now(timezone.utc), json.dumps(metadata or {}),
        )
        await self.append_event(run_id, "run_started", {"thread_id": thread_id})

    async def update_run(self, run_id: str, *, status: str, total_steps: int = 0, tokens: int = 0, cost_usd: float = 0.0, metadata: dict[str, Any] | None = None) -> None:
        if self._pool is None:
            return
        completed = datetime.now(timezone.utc) if status in {"completed", "rejected", "failed"} else None
        await self._pool.execute(
            "UPDATE agent_runs SET status=$2,total_steps=$3,token_estimate=$4,cost_usd_estimate=$5,completed_at=COALESCE($6,completed_at),metadata=metadata || $7::jsonb WHERE run_id=$1",
            run_id, status, total_steps, tokens, cost_usd, completed, json.dumps(metadata or {}),
        )

    async def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if self._pool is None:
            return
        await self._pool.execute(
            "INSERT INTO agent_run_events(run_id,event_type,event_payload) VALUES($1,$2,$3::jsonb)",
            run_id, event_type, json.dumps(payload, default=str),
        )

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        if self._pool is None:
            return None
        row = await self._pool.fetchrow("SELECT * FROM agent_runs WHERE run_id=$1", run_id)
        return dict(row) if row else None

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._pool is None:
            return []
        rows = await self._pool.fetch("SELECT * FROM agent_runs ORDER BY started_at DESC LIMIT $1", max(1, min(limit, 100)))
        return [dict(row) for row in rows]

    async def list_events(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return a bounded, chronological event stream for the operator dashboard."""
        if self._pool is None:
            return []
        rows = await self._pool.fetch(
            "SELECT event_id, run_id, event_type, event_payload, created_at "
            "FROM agent_run_events WHERE run_id=$1 ORDER BY created_at DESC LIMIT $2",
            run_id,
            max(1, min(limit, 500)),
        )
        events: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            payload = item.get("event_payload", {})
            if isinstance(payload, str):
                try:
                    item["event_payload"] = json.loads(payload)
                except json.JSONDecodeError:
                    item["event_payload"] = {"raw": payload[:2000]}
            events.append(item)
        return list(reversed(events))
