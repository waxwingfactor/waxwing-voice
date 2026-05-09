import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedOnlyMixin, UUIDMixin


class AuditLog(UUIDMixin, CreatedOnlyMixin, Base):
    """Append-only audit trail. Never update or delete rows.

    actor_type values: USER | SYSTEM | VOICE_AGENT
    action values (SCREAMING_SNAKE_CASE): DOCUMENT_UPLOAD, DOCUMENT_DELETE,
        DOCUMENT_REINDEX, BOOKING_CREATED, BOOKING_CANCELLED, EMAIL_SENT,
        HANDOFF_REQUESTED, SETTINGS_UPDATED, LEAD_CREATED
    """

    __tablename__ = "audit_logs"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable — some actions are company-level not property-level
    property_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("properties.id", ondelete="SET NULL"),
        index=True,
    )

    # Values: USER | SYSTEM | VOICE_AGENT
    actor_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # UUID of the user or "system" / "voice_agent" as a string
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)

    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # JSONB for context — must NOT contain PII (use entity IDs, not names/emails)
    metadata_: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata",
        JSONB,  # column name is "metadata" in DB; attribute is metadata_ to avoid clash
    )
