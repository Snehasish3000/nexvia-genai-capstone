"""Read-only Postgres inspection tool restricted to approved run-history tables."""
import json
import re
from typing import Any

from src.config import Settings
from src.telemetry.otel import observe_tool, span


class ReadOnlyQueryError(ValueError):
    """Raised when SQL exceeds the read-only run-history policy."""


class PostgresQueryService:
    ALLOWED_TABLES = {"agent_runs", "agent_run_events"}
    FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|execute|vacuum|attach|detach)\b", re.I)
    PRIVILEGED_FUNCTIONS = re.compile(
        r"\b(pg_[a-z0-9_]*|dblink[a-z0-9_]*|lo_import|lo_export|set_config|query_to_xml|table_to_xml|pg_advisory_[a-z0-9_]*)\s*\(",
        re.I,
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def validate_query(cls, query: str) -> str:
        normalized = query.strip().rstrip(";").strip()
        if not normalized or len(normalized) > 4000:
            raise ReadOnlyQueryError("Query must contain 1-4000 characters")
        if ";" in normalized:
            raise ReadOnlyQueryError("Multiple SQL statements are not permitted")
        if not re.match(r"^(select|with)\b", normalized, re.I) or cls.FORBIDDEN.search(normalized):
            raise ReadOnlyQueryError("Only read-only SELECT/CTE queries are allowed")
        if cls.PRIVILEGED_FUNCTIONS.search(normalized):
            raise ReadOnlyQueryError("Privileged PostgreSQL and large-object functions are blocked")
        tables = {name.lower() for name in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", normalized, re.I)}
        cte_aliases = {name.lower() for name in re.findall(r"(?:\bwith|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s+as\s*\(", normalized, re.I)}
        if not tables or not tables.issubset(cls.ALLOWED_TABLES | cte_aliases):
            raise ReadOnlyQueryError(f"Query may only read approved tables: {sorted(cls.ALLOWED_TABLES)}")
        return normalized

    @observe_tool("mcp.postgres.query")
    async def query(self, sql: str, limit: int = 100) -> list[dict[str, Any]]:
        statement = self.validate_query(sql)
        bounded_limit = max(1, min(int(limit), 250))
        try:
            import asyncpg
        except ImportError as exc:
            raise RuntimeError("asyncpg is required for the Postgres MCP tool") from exc
        with span("mcp.postgres.query", {"mcp.tool": "query_run_history"}):
            connection = await asyncpg.connect(self.settings.database_url, timeout=self.settings.database_connect_timeout, command_timeout=8)
            try:
                async with connection.transaction(readonly=True):
                    await connection.execute("SET LOCAL statement_timeout = '5000ms'")
                    rows = await connection.fetch(f"SELECT * FROM ({statement}) AS readonly_result LIMIT {bounded_limit}")
                    return [json.loads(json.dumps(dict(row), default=str)) for row in rows]
            finally:
                await connection.close()
