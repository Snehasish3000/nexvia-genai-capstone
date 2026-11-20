from datetime import UTC, datetime
from io import BytesIO

import pytest
from docx import Document

from app.ingestion import DocumentParser, HierarchicalChunker
from app.models import SourceDocument


def test_docx_parser_extracts_paragraphs_and_tables():
    document = Document()
    document.add_paragraph("Confidential data must be encrypted at rest.")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Control"
    table.rows[0].cells[1].text = "AES-256"
    stream = BytesIO()
    document.save(stream)
    parsed = DocumentParser().parse_bytes("control.docx", stream.getvalue(), ["engineering"])
    assert parsed[0].doc_type == "docx"
    assert "AES-256" in parsed[0].text


def test_chunker_adds_hierarchical_metadata():
    source = SourceDocument(
        source="policy.txt",
        text=" " .join(["Sensitive data requires strong controls."] * 120),
        doc_type="text",
        allowed_roles=["finance"],
        ingested_at=datetime.now(UTC),
    )
    chunks = HierarchicalChunker(child_size=40, child_overlap=5).split([source])
    assert len(chunks) > 1
    assert all(chunk.metadata["chunk_level"] == "child" for chunk in chunks)
    assert all(chunk.metadata["parent_id"] for chunk in chunks)
    assert all("parent_text" in chunk.metadata for chunk in chunks)
    assert all("admin" in chunk.metadata["allowed_roles"] for chunk in chunks)


def test_private_network_url_is_blocked():
    import asyncio

    from app.ingestion import _validate_public_url

    with pytest.raises(ValueError, match="local network|Private"):
        asyncio.run(_validate_public_url("http://127.0.0.1:8080/private"))
