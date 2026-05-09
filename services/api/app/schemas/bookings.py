"""Pydantic schemas for Booking endpoints — consumed by Alex (call and lead detail views)."""

import uuid
from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict


class BookingListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    property_id: uuid.UUID
    call_id: uuid.UUID | None
    tour_date: date | None
    start_time: time | None
    tour_type: str | None
    status: str
    confirmation_email_status: str
    created_at: datetime


class BookingDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    lead_id: uuid.UUID
    property_id: uuid.UUID
    company_id: uuid.UUID
    call_id: uuid.UUID | None
    calendar_provider: str
    calendar_event_id: str | None
    tour_date: date | None
    start_time: time | None
    end_time: time | None
    tour_type: str | None
    status: str
    confirmation_email_status: str
    created_at: datetime
    updated_at: datetime
