"""LangGraph persistence lifecycle: AsyncPostgresSaver in production, memory in tests."""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from src.config import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def checkpointer_context(settings: Settings) -> AsyncIterator[Any]:
    if not settings.persistence_enabled:
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()
        return
    try:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    except ImportError as exc:
        if settings.app_env == "production":
            raise RuntimeError("langgraph-checkpoint-postgres is required when production persistence is enabled") from exc
        from langgraph.checkpoint.memory import InMemorySaver

        logger.warning("Postgres checkpointer dependency not installed; using in-memory checkpoints")
        yield InMemorySaver()
        return
    try:
        serializer = None
        if settings.langgraph_aes_key:
            from langgraph.checkpoint.serde.encrypted import EncryptedSerializer

            serializer = EncryptedSerializer.from_pycryptodome_aes(settings.langgraph_aes_key.get_secret_value())
        if serializer is None:
            manager = AsyncPostgresSaver.from_conn_string(settings.database_url)
        else:
            manager = AsyncPostgresSaver.from_conn_string(settings.database_url, serde=serializer)
        async with manager as checkpointer:
            await checkpointer.setup()
            yield checkpointer
    except Exception:
        if settings.app_env == "production":
            raise
        from langgraph.checkpoint.memory import InMemorySaver

        logger.exception("Postgres checkpointer unavailable; falling back to in-memory checkpoints in non-production")
        yield InMemorySaver()
