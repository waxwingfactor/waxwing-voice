import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Call(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "calls"

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

    # Twilio's identifier for this call leg — unique per call
    twilio_call_sid: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    livekit_room_id: Mapped[str | None] = mapped_column(String(255))
    caller_phone: Mapped[str | None] = mapped_column(String(50))

    # Timestamps and duration stored explicitly for easy dashboard aggregation
    started_at: Mapped[str | None] = mapped_column(String(50))  # ISO8601 from Twilio
    ended_at: Mapped[str | None] = mapped_column(String(50))
    duration: Mapped[int | None] = mapped_column(Integer)  # seconds

    # AI-generated fields set by save_call_summary voice tool
    summary: Mapped[str | None] = mapped_column(Text)
    primary_intent: Mapped[str | None] = mapped_column(String(100))
    sentiment: Mapped[str | None] = mapped_column(String(50))
    # JSONB list of action item strings: ["[TOUR] Book follow-up", ...]
    action_items: Mapped[list[str] | None] = mapped_column(JSONB)
    next_steps: Mapped[str | None] = mapped_column(Text)

    # Values: active | completed | failed | abandoned
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")
    # Values: none | requested | escalated
    escalation_status: Mapped[str] = mapped_column(String(50), nullable=False, default="none")
    escalation_flag: Mapped[bool] = mapped_column(nullable=False, default=False)

    recording_url: Mapped[str | None] = mapped_column(String(500))  # disabled by default

    # Snapshot of lead fields extracted during the call — set by save_call_summary
    lead_fields_extracted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
