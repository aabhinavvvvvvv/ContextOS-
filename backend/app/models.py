"""Database tables, defined as SQLAlchemy ORM models.

After changing a model, create a migration (from backend/):
    uv run alembic revision --autogenerate -m "describe the change"
then review the generated file before running `uv run alembic upgrade head`.
"""

import uuid
from datetime import date, datetime
from typing import Any, Literal, get_args

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.embeddings import EMBEDDING_DIM

# Postgres text search configuration: English stemming ("running" -> "run") and stop words.
# Queries must use the same configuration as the stored search_vector.
TEXT_SEARCH_CONFIG = "english"

# Give every constraint a predictable name, so later migrations can find and alter it.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",  # all columns, e.g. uq_chunks_document_id_chunk_index
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ix": "ix_%(column_0_label)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Connector(Base):
    """A data source synced into documents: a GitHub repository or a local folder."""

    __tablename__ = "connectors"
    __table_args__ = (
        CheckConstraint("kind IN ('github', 'folder')", name="kind_valid"),
        CheckConstraint("last_status IN ('never', 'ok', 'error')", name="last_status_valid"),
        UniqueConstraint("kind", "name"),  # the same repo/folder can't be added twice
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)  # "owner/repo" or the folder's relative path
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Start time of the last successful sync: the next GitHub sync asks only for items
    # updated since then ("since" polling).
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(Text, default="never", server_default="never")
    last_error: Mapped[str | None] = mapped_column(Text)
    documents_changed: Mapped[int] = mapped_column(default=0, server_default="0")  # last sync


class Document(Base):
    """A file (uploaded, or produced by a connector) and the state of its ingestion pipeline."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="status_valid",
        ),
        CheckConstraint("source IN ('upload', 'github', 'folder')", name="source_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Hex SHA-256 of the file bytes; unique, so the same file is never ingested twice.
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(Text, default="pending", server_default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Where it came from. Connector items have a stable key ("github:owner/repo#12"):
    # when the item changes, the document with that key is replaced, never duplicated.
    source: Mapped[str] = mapped_column(Text, default="upload", server_default="upload")
    source_key: Mapped[str | None] = mapped_column(Text, unique=True)
    source_url: Mapped[str | None] = mapped_column(Text)  # link to the original (GitHub page)
    # CASCADE: removing a connector removes everything it synced.
    connector_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("connectors.id", ondelete="CASCADE"), index=True
    )


class DocumentChunk(Base):
    """One retrievable piece of a document's text, in reading order."""

    __tablename__ = "chunks"
    __table_args__ = (
        # Also serves as the index for "all chunks of document X, in order".
        UniqueConstraint("document_id", "chunk_index"),
        # Approximate nearest-neighbour index for semantic search. vector_cosine_ops must
        # match the operator queries use (<=>, cosine distance). m / ef_construction are
        # pgvector's defaults, written out: higher = better recall, slower build, more memory.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        # Inverted index (lexeme -> rows) for keyword search on search_vector.
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int]
    text: Mapped[str] = mapped_column(Text)
    heading: Mapped[str | None] = mapped_column(Text)
    page_number: Mapped[int | None]
    # The two large columns are deferred: not loaded with the row unless a query selects
    # them explicitly. deferred_raiseload makes an accidental read raise an error instead
    # of silently running a hidden query (which fails in async code anyway).

    # Embedding of chunk_embedding_text(heading, text); used for semantic search.
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), deferred=True, deferred_raiseload=True
    )
    # Keyword-search lexemes, computed by Postgres itself (never written by Python):
    # heading words weighted A (more important), body words B.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            f"setweight(to_tsvector('{TEXT_SEARCH_CONFIG}', coalesce(heading, '')), 'A') || "
            f"setweight(to_tsvector('{TEXT_SEARCH_CONFIG}', text), 'B')",
            persisted=True,
        ),
        deferred=True,
        deferred_raiseload=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Conversation(Base):
    """A thread of questions and answers: the raw record that memories are extracted from."""

    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(Text)  # the first question, shortened
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Bumped on every new turn, so lists can show the most recently active thread first.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Message(Base):
    """One side of a turn: the user's question or the assistant's answer."""

    __tablename__ = "messages"
    __table_args__ = (
        # Order within a conversation. Not created_at: a question and its answer are saved in
        # one transaction, and Postgres's now() is the same for the whole transaction.
        # Unique, so two concurrent turns can never claim the same slot.
        UniqueConstraint("conversation_id", "position"),
        CheckConstraint("role IN ('user', 'assistant')", name="role_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE")
    )
    position: Mapped[int]  # 0, 1, 2, ... within the conversation
    role: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    # Assistant messages: the sources it cited, as the user saw them. A copy, not a foreign
    # key to chunks: chunks are deleted and recreated on re-ingestion, but this record of
    # "what the answer was based on" must not change or disappear.
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    # Assistant messages: the memories it cited as [Mn], copied for the same reason.
    memory_citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # User messages: when long-term memories were extracted from this turn (NULL = not yet).
    # Makes extraction idempotent, and lets a sweep find turns that still need it.
    memories_extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# One source of truth for memory kinds: the CHECK constraint and the API/LLM schemas use it.
MemoryKind = Literal["fact", "decision", "preference", "task"]
MEMORY_KINDS: tuple[str, ...] = get_args(MemoryKind)


class Memory(Base):
    """A durable, standalone statement extracted from a conversation (semantic memory).

    e.g. kind="preference", content="The user prefers short answers."
    """

    __tablename__ = "memories"
    __table_args__ = (
        CheckConstraint(
            "kind IN (" + ", ".join(f"'{kind}'" for kind in MEMORY_KINDS) + ")",
            name="kind_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(Text)
    content: Mapped[str] = mapped_column(Text)
    # Embedding of `content`, for finding relevant memories by meaning (used from Phase 3.5).
    embedding: Mapped[list[float]] = mapped_column(
        Vector(EMBEDDING_DIM), deferred=True, deferred_raiseload=True
    )
    # Provenance: the user message whose turn this was extracted from. CASCADE: deleting a
    # conversation also forgets what was learned from it. Indexed: Postgres doesn't index
    # foreign keys automatically, and the cascade has to find these rows.
    source_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Set when a newer memory replaces this one ("keep Gemini" → "switched to Claude").
    # The old memory is kept, not deleted, so past beliefs stay visible. NULL = still current.
    superseded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memories.id", ondelete="SET NULL")
    )
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Time-bound memories ("on vacation this week"): the last day they are true. After it,
    # the memory is no longer recalled. NULL = true until something replaces it.
    expires_on: Mapped[date | None] = mapped_column(Date)
