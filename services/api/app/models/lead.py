import uuid
from datetime import date
from typing import Any

from sqlalchemy import Date, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Lead(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "leads"

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
    # Nullable — a lead can exist before a call is linked (future: web form capture)
    call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("calls.id", ondelete="SET NULL"),
        index=True,
    )

    # Qualification fields
    name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50), index=True)
    email: Mapped[str | None] = mapped_column(String(320))
    budget: Mapped[float | None] = mapped_column(Numeric(10, 2))
    move_in_date: Mapped[date | None] = mapped_column(Date)
    desired_unit_type: Mapped[str | None] = mapped_column(String(100))
    # pet_info: {"has_pets": true, "breed": "Labrador", "weight_lbs": 65}
    pet_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    number_of_occupants: Mapped[int | None] = mapped_column()
    reason_for_moving: Mapped[str | None] = mapped_column(Text)
    how_heard: Mapped[str | None] = mapped_column(String(255))

    # Values: low | medium | high | immediate
    urgency: Mapped[str | None] = mapped_column(String(50))
    tour_interest: Mapped[bool] = mapped_column(nullable=False, default=False)

    # Values: hot | warm | cold (set by voice agent via create_or_update_lead)
    lead_score: Mapped[str | None] = mapped_column(String(20))
    # Values: new | contacted | toured | applied | closed | lost
    lead_status: Mapped[str] = mapped_column(String(50), nullable=False, default="new")
