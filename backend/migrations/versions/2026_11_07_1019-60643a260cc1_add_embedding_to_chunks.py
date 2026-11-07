"""add embedding to chunks

Revision ID: 60643a260cc1
Revises: 9d4dec045cfc
Create Date: 2026-11-07 10:19:44.442694

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


# revision identifiers, used by Alembic.
revision: str = '60643a260cc1'
down_revision: Union[str, Sequence[str], None] = '9d4dec045cfc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Data step first: existing chunks have no embedding, and a NOT NULL column can't be
    # added to rows that lack a value. Chunks are derived data (rebuildable from the stored
    # upload), so delete them and send their documents back to "pending"; the worker's
    # sweep re-ingests them, this time with embeddings.
    op.execute("DELETE FROM chunks")
    op.execute("UPDATE documents SET status = 'pending', error = NULL WHERE status = 'ready'")
    # 384 is written out, not imported from app.embeddings: a migration is a frozen record
    # of a past change and must not shift if the app's constant changes later.
    op.add_column("chunks", sa.Column("embedding", Vector(384), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    # Deleted chunks aren't restored here; documents left "pending" are re-ingested.
    op.drop_column("chunks", "embedding")
