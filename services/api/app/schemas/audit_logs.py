"""Pydantic schemas for AuditLog endpoints — consumed by Alex (admin / compliance views).

The underlying ORM model uses `metadata_` for the JSONB column because
`metadata` collides with SQLAlchemy's `DeclarativeBase.metadata`. We expose
it on the wire as `metadata` and use `validation_alias` so Pydantic reads
the ORM attribute `metadata_` while emitting the field as `metadata` in JSON.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class AuditLogItem(BaseModel):
    """One audit-log row.

    Notes:
        - `actor_id` is stored as a string (not a UUID) because some actors
          are not users — e.g. "voice_agent" or "system".
        - `entity_id` is also a string for the same reason.
        - `metadata` is the wire name; the ORM attribute is `metadata_`.
          We use `validation_alias` (not `alias`) so the JSON output stays
          `metadata` while validation reads from `metadata_`.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    company_id: uuid.UUID
    property_id: uuid.UUID | None
    actor_type: str  # USER | SYSTEM | VOICE_AGENT
    actor_id: str
    action: str  # SCREAMING_SNAKE_CASE; e.g. TOUR_BOOKED, EMAIL_SENT
    entity_type: str  # call | lead | booking | email_record | document | property
    entity_id: str
    metadata: dict[str, Any] | None = Field(
        default=None,
        validation_alias=AliasChoices("metadata_", "metadata"),
    )
    created_at: datetime
