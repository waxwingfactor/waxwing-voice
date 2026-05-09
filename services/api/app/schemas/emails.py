"""Pydantic schemas for Email endpoints — consumed by Alex (call and lead detail views)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EmailListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID | None
    call_id: uuid.UUID | None
    recipient: str
    subject: str
    template_type: str | None
    delivery_status: str
    sent_at: datetime | None
    created_at: datetime


class EmailDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    company_id: uuid.UUID
    lead_id: uuid.UUID | None
    call_id: uuid.UUID | None
    recipient: str
    subject: str
    body: str
    template_type: str | None
    delivery_provider: str | None
    delivery_status: str
    sent_at: datetime | None
    created_at: datetime
    updated_at: datetime
