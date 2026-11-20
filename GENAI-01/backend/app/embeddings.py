"""Pluggable dense embeddings: deterministic local hashing for tests and OpenAI for production."""

import hashlib
import math
import re
from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError("Embedding providers must implement query embedding")

    @abstractmethod
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError("Embedding providers must implement document embedding")


class HashEmbeddingProvider(EmbeddingProvider):
    """Dependency-free feature hashing; for local smoke tests, never a production semantic model."""

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        tokens = re.findall(r"[a-z0-9_]+", text.casefold())
        vector = [0.0] * self.dimensions
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        return [value / norm for value in vector] if norm else vector

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, api_key: str, model: str, dimensions: int | None = None) -> None:
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions

    async def _embed_many(self, texts: list[str]) -> list[list[float]]:
        kwargs: dict[str, object] = {"model": self.model, "input": texts}
        if self.dimensions is not None and self.model.startswith("text-embedding-3"):
            kwargs["dimensions"] = self.dimensions
        response = await self.client.embeddings.create(**kwargs)
        return [list(item.embedding) for item in sorted(response.data, key=lambda row: row.index)]

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed_many([text]))[0]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._embed_many(texts)


def build_embedding_provider(settings: object) -> EmbeddingProvider:
    provider = getattr(settings, "embedding_provider")
    if provider == "openai":
        return OpenAIEmbeddingProvider(
            api_key=getattr(settings, "openai_api_key"),
            model=getattr(settings, "embedding_model"),
            dimensions=getattr(settings, "vector_size"),
        )
    return HashEmbeddingProvider(dimensions=getattr(settings, "vector_size"))
