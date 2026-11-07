"""Database tables, defined as SQLAlchemy ORM models.

After changing a model, create a migration (from backend/):
    uv run alembic revision --autogenerate -m "describe the change"
then review the generated file before running `uv run alembic upgrade head`.
"""

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
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


class Document(Base):
    """An uploaded file and the state of its ingestion pipeline."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="status_valid",
        ),
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
