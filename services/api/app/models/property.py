import uuid
from typing import Any

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class Property(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "properties"

    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    # JSONB for flexible dict fields — avoids premature normalization in MVP
    # amenities: {"pool": true, "gym": true, "parking": "covered", ...}
    amenities: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # office_hours: {"mon-fri": "9am-6pm", "sat": "10am-4pm", "sun": "closed"}
    office_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    leasing_policies: Mapped[str | None] = mapped_column(Text)
    maintenance_instructions: Mapped[str | None] = mapped_column(Text)
    # escalation_contacts: [{"name": "Jane", "phone": "...", "role": "PM"}]
    escalation_contacts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    # business_hour_rules: {"timezone": "America/New_York", "rules": [...]}
    business_hour_rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # call_handling_rules: {"after_hours_message": "...", "escalate_after_seconds": 30}
    call_handling_rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
