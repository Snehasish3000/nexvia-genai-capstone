"""SQLite-backed feedback, query telemetry and role-scoped semantic response cache."""

import json
import math
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class ApplicationDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    doc_type TEXT NOT NULL,
                    allowed_roles TEXT NOT NULL,
                    chunks_created INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK(rating IN ('up','down')),
                    comment TEXT,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS query_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    latency_ms REAL NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    completion_tokens INTEGER NOT NULL,
                    estimated_cost_usd REAL NOT NULL,
                    retrieval_mode TEXT NOT NULL,
                    cache_hit INTEGER NOT NULL,
                    abstained INTEGER NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS response_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    normalized_query TEXT NOT NULL,
                    query_vector TEXT NOT NULL,
                    role TEXT NOT NULL,
                    cache_scope TEXT NOT NULL DEFAULT 'default',
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cache_role_created ON response_cache(role, cache_scope, created_at);
                CREATE INDEX IF NOT EXISTS idx_metrics_created ON query_metrics(created_at);
                CREATE INDEX IF NOT EXISTS idx_feedback_request ON feedback(request_id);
                """
            )
            cache_columns = {row[1] for row in connection.execute("PRAGMA table_info(response_cache)").fetchall()}
            if "cache_scope" not in cache_columns:
                connection.execute("ALTER TABLE response_cache ADD COLUMN cache_scope TEXT NOT NULL DEFAULT 'default'")

    def save_document(self, document_id: str, source: str, doc_type: str, allowed_roles: list[str], chunks_created: int) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM documents WHERE source=?", (source,))
            connection.execute(
                "INSERT INTO documents(document_id,source,doc_type,allowed_roles,chunks_created,created_at) VALUES(?,?,?,?,?,?)",
                (document_id, source, doc_type, json.dumps(allowed_roles), chunks_created, time.time()),
            )

    def document_count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0])

    def save_feedback(self, request_id: str, user_id: str, role: str, rating: str, comment: str | None) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO feedback(request_id,user_id,role,rating,comment,created_at) VALUES(?,?,?,?,?,?)",
                (request_id, user_id, role, rating, comment, time.time()),
            )

    def record_query(
        self,
        request_id: str,
        user_id: str,
        role: str,
        latency_ms: float,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_usd: float,
        retrieval_mode: str,
        cache_hit: bool,
        abstained: bool,
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO query_metrics(
                    request_id,user_id,role,latency_ms,prompt_tokens,completion_tokens,
                    estimated_cost_usd,retrieval_mode,cache_hit,abstained,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    request_id, user_id, role, latency_ms, prompt_tokens, completion_tokens,
                    estimated_cost_usd, retrieval_mode, int(cache_hit), int(abstained), time.time(),
                ),
            )

    def cache_lookup(
        self,
        normalized_query: str,
        query_vector: list[float],
        role: str,
        threshold: float,
        ttl_seconds: int,
        cache_scope: str = "default",
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT normalized_query,query_vector,response_json,created_at FROM response_cache WHERE role=? AND cache_scope=? ORDER BY created_at DESC LIMIT 500",
                (role, cache_scope),
            ).fetchall()
        best_response: dict[str, Any] | None = None
        best_similarity = -1.0
        for row in rows:
            age = now - float(row["created_at"])
            if age > ttl_seconds:
                continue
            if row["normalized_query"] == normalized_query:
                similarity = 1.0
            else:
                cached_vector = json.loads(row["query_vector"])
                similarity = self._cosine(query_vector, cached_vector)
            if similarity >= threshold and similarity > best_similarity:
                best_similarity = similarity
                best_response = json.loads(row["response_json"])
        return best_response

    def cache_save(
        self,
        normalized_query: str,
        query_vector: list[float],
        role: str,
        response: dict[str, Any],
        cache_scope: str = "default",
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO response_cache(normalized_query,query_vector,role,cache_scope,response_json,created_at) VALUES(?,?,?,?,?,?)",
                (normalized_query, json.dumps(query_vector), role, cache_scope, json.dumps(response), time.time()),
            )
            connection.execute(
                "DELETE FROM response_cache WHERE id NOT IN (SELECT id FROM response_cache ORDER BY created_at DESC LIMIT 2000)"
            )

    def clear_cache(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM response_cache")

    def cache_count(self) -> int:
        with self._lock, self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM response_cache").fetchone()[0])

    def metrics_summary(self) -> dict[str, float | int]:
        with self._lock, self._connect() as connection:
            metrics = connection.execute(
                "SELECT COUNT(*) AS total, COALESCE(AVG(latency_ms),0) AS avg_latency, COALESCE(SUM(estimated_cost_usd),0) AS cost FROM query_metrics"
            ).fetchone()
            feedback_count = connection.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        return {
            "total_queries": int(metrics["total"]),
            "avg_latency_ms": float(metrics["avg_latency"]),
            "total_estimated_cost_usd": float(metrics["cost"]),
            "feedback_count": int(feedback_count),
        }

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right) or not left:
            return 0.0
        dot = sum(a * b for a, b in zip(left, right, strict=True))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return max(-1.0, min(1.0, dot / (left_norm * right_norm)))
