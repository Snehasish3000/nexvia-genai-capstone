"""Mixed-format ingestion, LlamaIndex chunking and parent-child metadata construction."""

import asyncio
import hashlib
import ipaddress
import logging
import re
import socket
import uuid
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.embeddings import EmbeddingProvider
from app.models import Chunk, SourceDocument
from app.retrieval import InMemoryVectorStore, QdrantVectorStore
from app.tracing import traceable

logger = logging.getLogger(__name__)


class DocumentParser:
    """Parses PDF page-by-page, DOCX paragraphs/tables, HTML and plain text."""

    def parse_bytes(self, filename: str, content: bytes, allowed_roles: list[str]) -> list[SourceDocument]:
        suffix = Path(filename).suffix.casefold()
        source = Path(filename).name
        now = datetime.now(UTC)
        if suffix == ".pdf":
            from pypdf import PdfReader

            reader = PdfReader(BytesIO(content))
            documents = []
            for page_number, page in enumerate(reader.pages, start=1):
                text = (page.extract_text() or "").strip()
                if text:
                    documents.append(
                        SourceDocument(
                            source=source, text=text, doc_type="pdf", page=page_number,
                            allowed_roles=allowed_roles, ingested_at=now,
                        )
                    )
            if not documents:
                raise ValueError("The PDF contains no extractable text; run OCR before ingestion")
            return documents
        if suffix == ".docx":
            from docx import Document as DocxDocument

            document = DocxDocument(BytesIO(content))
            sections = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    sections.append(" | ".join(cell.text.strip() for cell in row.cells))
            text = "\n".join(sections).strip()
            if not text:
                raise ValueError("The DOCX file contains no readable text")
            return [SourceDocument(source=source, text=text, doc_type="docx", allowed_roles=allowed_roles, ingested_at=now)]
        if suffix in {".html", ".htm"}:
            soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")
            for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                element.decompose()
            text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
            if not text:
                raise ValueError("The HTML file contains no readable text")
            return [SourceDocument(source=source, text=text, doc_type="html", allowed_roles=allowed_roles, ingested_at=now)]
        if suffix in {".txt", ".md"}:
            text = content.decode("utf-8", errors="replace").strip()
            if not text:
                raise ValueError("The text file is empty")
            return [SourceDocument(source=source, text=text, doc_type="text", allowed_roles=allowed_roles, ingested_at=now)]
        raise ValueError("Unsupported file type. Supported formats: .pdf, .docx, .html, .htm, .txt, .md")

    async def parse_url(self, url: str, allowed_roles: list[str]) -> SourceDocument:
        await _validate_public_url(url)
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            follow_redirects=False,
            headers={"User-Agent": "NexviaRAGCapstone/1.0 (educational project)"},
        ) as client:
            current_url = url
            response: httpx.Response | None = None
            for _ in range(6):
                await _validate_public_url(current_url)
                response = await client.get(current_url)
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("location")
                if not location:
                    raise ValueError("The source URL returned a redirect without a Location header")
                current_url = urljoin(str(response.url), location)
            else:
                raise ValueError("The source URL exceeded the redirect limit")
            if response is None:
                raise ValueError("The source URL did not return a response")
            response.raise_for_status()
        final_url = str(response.url)
        soup = BeautifulSoup(response.text, "html.parser")
        for element in soup(["script", "style", "nav", "footer", "header", "noscript"]):
            element.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else urlparse(final_url).netloc
        text = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
        if not text:
            raise ValueError("The URL returned no extractable page text")
        return SourceDocument(
            source=title[:250], text=text, doc_type="url", allowed_roles=allowed_roles,
            ingested_at=datetime.now(UTC), source_uri=final_url,
        )


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) URLs are accepted")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not accepted")
    host = parsed.hostname.casefold()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Private and local network destinations are not accepted")
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo, host, parsed.port or (443 if parsed.scheme == "https" else 80), 0, socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError("The URL host could not be resolved") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Private, loopback, link-local and reserved IP destinations are not accepted")


class HierarchicalChunker:
    """Sentence-level child chunks grouped into adjacent parent windows for context expansion."""

    def __init__(self, child_size: int = 450, child_overlap: int = 64, children_per_parent: int = 3) -> None:
        self.child_size = child_size
        self.child_overlap = child_overlap
        self.children_per_parent = children_per_parent

    def _llamaindex_nodes(self, documents: list[SourceDocument]) -> list[tuple[str, dict[str, Any]]]:
        try:
            from llama_index.core import Document
            from llama_index.core.ingestion import IngestionPipeline
            from llama_index.core.node_parser import SentenceSplitter
        except ImportError:
            return self._fallback_nodes(documents)
        llama_documents = [
            Document(
                text=document.text,
                metadata={
                    "source": document.source,
                    "page": document.page,
                    "doc_type": document.doc_type,
                    "allowed_roles": document.allowed_roles,
                    "ingested_at": document.ingested_at.isoformat(),
                    "source_uri": document.source_uri,
                },
            )
            for document in documents
        ]
        pipeline = IngestionPipeline(
            transformations=[SentenceSplitter(chunk_size=self.child_size, chunk_overlap=self.child_overlap)]
        )
        nodes = pipeline.run(documents=llama_documents)
        return [(node.get_content(), dict(node.metadata)) for node in nodes if node.get_content().strip()]

    def _fallback_nodes(self, documents: list[SourceDocument]) -> list[tuple[str, dict[str, Any]]]:
        nodes: list[tuple[str, dict[str, Any]]] = []
        for document in documents:
            words = re.findall(r"\S+", document.text)
            cursor = 0
            while cursor < len(words):
                end = min(len(words), cursor + self.child_size)
                text = " ".join(words[cursor:end])
                metadata = {
                    "source": document.source,
                    "page": document.page,
                    "doc_type": document.doc_type,
                    "allowed_roles": document.allowed_roles,
                    "ingested_at": document.ingested_at.isoformat(),
                    "source_uri": document.source_uri,
                    "chunker": "fallback-window",
                }
                if text.strip():
                    nodes.append((text, metadata))
                if end == len(words):
                    break
                cursor = max(cursor + 1, end - self.child_overlap)
        return nodes

    def split(self, documents: list[SourceDocument]) -> list[Chunk]:
        nodes = self._llamaindex_nodes(documents)
        grouped: dict[tuple[str, Any], list[tuple[str, dict[str, Any]]]] = {}
        for text, metadata in nodes:
            grouped.setdefault((str(metadata.get("source", "unknown")), metadata.get("page")), []).append((text, metadata))
        chunks: list[Chunk] = []
        for (source, page), rows in grouped.items():
            parent_windows: dict[int, str] = {}
            for start in range(0, len(rows), self.children_per_parent):
                parent_index = start // self.children_per_parent
                parent_windows[parent_index] = " ".join(text for text, _ in rows[start:start + self.children_per_parent])
            for index, (text, metadata) in enumerate(rows):
                parent_index = index // self.children_per_parent
                parent_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}:{page}:{parent_index}"))
                safe_roles = sorted(set(metadata.get("allowed_roles") or ["admin"]) | {"admin"})
                enriched = {
                    **metadata,
                    "source": source,
                    "page": page,
                    "allowed_roles": safe_roles,
                    "parent_id": parent_id,
                    "parent_text": parent_windows[parent_index],
                    "chunk_level": "child",
                }
                identity = hashlib.sha256(f"{source}|{page}|{index}|{text}".encode()).hexdigest()
                chunks.append(Chunk(chunk_id=identity, text=text, metadata=enriched))
        return chunks


class IngestionService:
    def __init__(
        self,
        store: InMemoryVectorStore | QdrantVectorStore,
        embeddings: EmbeddingProvider,
        cache_database: Any,
        chunker: HierarchicalChunker | None = None,
    ) -> None:
        self.store = store
        self.embeddings = embeddings
        self.cache_database = cache_database
        self.chunker = chunker or HierarchicalChunker()
        self.parser = DocumentParser()

    @traceable(name="llamaindex_document_ingestion", run_type="chain")
    async def ingest_sources(self, documents: list[SourceDocument]) -> tuple[str, int, str]:
        if not documents:
            raise ValueError("At least one source document is required")
        chunks = await asyncio.to_thread(self.chunker.split, documents)
        if not chunks:
            raise ValueError("The source produced no searchable chunks")
        vectors = await self.embeddings.embed_documents([chunk.text for chunk in chunks])
        source_names = {str(chunk.metadata.get("source", "")) for chunk in chunks}
        await self.store.delete_sources(source_names)
        await self.store.upsert(chunks, vectors)
        self.cache_database.clear_cache()
        document_id = str(uuid.uuid4())
        source = documents[0].source
        logger.info(
            "Ingestion completed",
            extra={"event": "document_ingested", "document_id": document_id, "chunks_created": len(chunks)},
        )
        return document_id, len(chunks), source
