import uuid
from datetime import date, time

from sqlalchemy import Date, ForeignKey, String, Time
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Booking(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "bookings"

    lead_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("leads.id", ondelete="CASCADE"),
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
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="SET NULL"),
        index=True,
    )

    # Values: google_calendar (first; others post-MVP)
    calendar_provider: Mapped[str] = mapped_column(String(50), nullable=False, default="google_calendar")
    calendar_event_id: Mapped[str | None] = mapped_column(String(255))

    tour_date: Mapped[date | None] = mapped_column(Date)
    start_time: Mapped[time | None] = mapped_column(Time)
    end_time: Mapped[time | None] = mapped_column(Time)
    # Values: in_person | self_guided | virtual
    tour_type: Mapped[str | None] = mapped_column(String(50))

    # Values: confirmed | cancelled | rescheduled | no_show | completed
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="confirmed")
    # Values: pending | sent | failed
    confirmation_email_status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
