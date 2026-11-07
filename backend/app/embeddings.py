"""Text embeddings with a local ONNX model (fastembed).

The only module that imports fastembed, so changing the model or library touches one place.
Changing the model also means re-embedding every chunk (vectors from different models
are not comparable) and possibly a migration if the dimension changes. That's why the model
is a constant here and not a setting in .env.

Model choice (2026-11-07, measured on ContextOS's own docs): bge-small-en-v1.5 tied for the
best retrieval accuracy (8/10 top-1, 10/10 top-3) with the lowest memory use (~650 MB peak),
and it reads up to 512 tokens, enough for our ~375-token chunks.
"""

from collections.abc import Sequence
from pathlib import Path

from fastembed import TextEmbedding

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIM = 384


class Embedder:
    """Loads the model once (slow, a few hundred MB of RAM), then embeds many texts.

    Embedding is CPU-heavy and blocking: call these methods from a worker thread in async code.
    """

    def __init__(self, cache_dir: Path) -> None:
        self._model = TextEmbedding(EMBEDDING_MODEL, cache_dir=str(cache_dir))

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for stored text (chunks)."""
        return [vector.tolist() for vector in self._model.passage_embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        """Vector for a search query. Queries get a model-specific prefix, handled by fastembed."""
        [vector] = self._model.query_embed([text])
        return vector.tolist()


def chunk_embedding_text(heading: str | None, text: str) -> str:
    """What we embed for a chunk: its heading path, then its text.

    The heading gives short chunks their meaning back: "PostgreSQL, pgvector, Redis" alone
    is vague; under "Technology Stack > Database and Storage" it's clear.
    """
    return f"{heading}\n\n{text}" if heading else text
