import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

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
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    file_name: Mapped[str] = mapped_column(String(500), nullable=False)
    # Values: pdf | docx | txt
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # S3 object key: {property_id}/{uuid}/{original_filename}
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    # Path to the extracted .txt sidecar in S3
    extracted_text_ref: Mapped[str | None] = mapped_column(String(1000))

    # Values: uploaded | failed
    upload_status: Mapped[str] = mapped_column(String(50), nullable=False, default="uploaded")
    # Values: pending | processing | indexing | indexed | failed | retrying
    processing_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    processing_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))

    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
