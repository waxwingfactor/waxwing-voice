"""TranscriptSegment — append-only record of a single utterance during a call.

Consumed by Akhil (voice agent inserts via POST /v1/voice/transcript-segment)
and Alex (dashboard call detail view via GET /v1/calls/{id}).
"""

import uuid

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedOnlyMixin, UUIDMixin


class TranscriptSegment(UUIDMixin, CreatedOnlyMixin, Base):
    """One speaker turn in a call transcript.

    Segments are append-only — they are never updated after insertion.
    Ordered by `timestamp` (seconds since call start) for display.
    """

    __tablename__ = "transcript_segments"

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Values: "agent" | "caller"
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Seconds since call start — from LiveKit/Whisper
    timestamp: Mapped[float] = mapped_column(Float, nullable=False)
