import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedOnlyMixin, UUIDMixin

# 1536 matches text-embedding-ada-002 and Gemini embedding-001.
# Update this constant when the embedding model is finalized with Subbu.
EMBEDDING_DIMENSIONS = 1536


class KnowledgeChunk(UUIDMixin, CreatedOnlyMixin, Base):
    """Append-only — chunks are deleted and recreated on re-index, never updated."""

    __tablename__ = "knowledge_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    property_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    # Human-readable label for citations: "Unit Pricing Guide, p.3"
    source_label: Mapped[str] = mapped_column(String(500), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)

    # pgvector column — retrieval queries must always filter by property_id
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS), nullable=False)
