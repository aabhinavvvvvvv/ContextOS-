"""Test helpers shared by several test files (fixtures live in conftest.py)."""

import hashlib
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import get_settings
from app.embeddings import EMBEDDING_DIM, Embedder
from app.ingestion.service import ingest_document
from app.llm import LLMResult


class FakeEmbedder:
    """Stands in for app.embeddings.Embedder without loading a model.

    Same text -> same vector (derived from its SHA-256), with the real dimension.
    The numbers carry no meaning, so use the real model to test search quality.
    """

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return [self.vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.vector(text)

    @staticmethod
    def vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode()).digest()  # 32 bytes
        return [byte / 255 for byte in digest] * (EMBEDDING_DIM // len(digest))


def make_pdf(page_texts: list[str]) -> bytes:
    """Build a minimal valid PDF with one line of text per page (no parentheses in text).

    A PDF is a list of numbered objects plus an "xref" table of each object's byte offset.
    """
    page_ids = [4 + 2 * i for i in range(len(page_texts))]
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {len(page_texts)} >>".encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_id, text in zip(page_ids, page_texts):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id + 1} 0 R >>".encode()
        )
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_offset = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        pdf += b"%010d 00000 n \n" % offset
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref_offset,
    )
    return bytes(pdf)


class InlineJobQueue:
    """Test stand-in for app.jobs.JobQueue: runs ingestion immediately, without Redis.

    Memory extraction is only recorded, not run: it would consume the scripted replies of
    the FakeLLM that tests set up for answering. Memory tests call remember_turn directly.
    """

    def __init__(self, engine: AsyncEngine, embedder: Embedder | FakeEmbedder) -> None:
        self.engine = engine
        self.embedder = embedder
        self.memory_message_ids: list[uuid.UUID] = []

    async def enqueue_memory_extraction(self, message_id: uuid.UUID) -> bool:
        self.memory_message_ids.append(message_id)
        return True

    async def enqueue_ingestion(self, document_id: uuid.UUID) -> bool:
        await ingest_document(self.engine, document_id, get_settings().upload_dir, self.embedder)
        return True


class FakeLLM:
    """Stands in for app.llm.LLM: returns scripted replies and records every prompt sent.

    `answer` is one reply for every call, or a list replied in order (the last one repeats),
    e.g. ["<rewritten question>", "<answer>"] for a follow-up.
    """

    def __init__(
        self, answer: str | list[str] = "Scripted answer [1].", error: Exception | None = None
    ) -> None:
        self.replies = [answer] if isinstance(answer, str) else list(answer)
        self.error = error
        self.calls: list[tuple[str, str]] = []  # (system, prompt) per call
        self.json_schemas: list[dict] = []  # the schema of each JSON-mode call

    async def generate(
        self,
        *,
        system: str,
        prompt: str,
        max_output_tokens: int = 1024,
        json_schema: dict | None = None,
    ) -> LLMResult:
        self.calls.append((system, prompt))
        if json_schema is not None:
            self.json_schemas.append(json_schema)
        if self.error is not None:
            raise self.error
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return LLMResult(text=text, input_tokens=100, output_tokens=10, thinking_tokens=0)
