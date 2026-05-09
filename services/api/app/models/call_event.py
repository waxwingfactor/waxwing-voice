"""CallEvent — append-only structured event emitted during a call.

Consumed by Akhil (voice agent inserts via POST /v1/voice/events)
and Alex (dashboard call detail view via GET /v1/calls/{id}).
"""

import uuid
from typing import Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedOnlyMixin, UUIDMixin


class CallEvent(UUIDMixin, CreatedOnlyMixin, Base):
    """A structured event emitted by the voice agent during a call.

    Examples: call_started, lead_captured, tour_booked, escalated.
    Append-only — never updated after insertion.
    """

    __tablename__ = "call_events"

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SCREAMING_SNAKE_CASE recommended; validated against CallEventType enum in schema
    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # Arbitrary JSON context — must not contain PII (use IDs not names/emails)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # ISO 8601 string from the voice agent — stored as-is for fidelity
    occurred_at: Mapped[str] = mapped_column(String(50), nullable=False)
