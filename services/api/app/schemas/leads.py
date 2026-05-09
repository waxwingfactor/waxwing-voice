"""Pydantic schemas for Lead endpoints — consumed by Alex (dashboard).

NEEDS ALEX REVIEW: confirm which filters are needed on GET /v1/leads/ before Phase 2.
"""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LeadCreateRequest(BaseModel):
    """Used by the create_or_update_lead voice tool and future web form captures."""

    property_id: uuid.UUID
    call_id: uuid.UUID | None = None
    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    budget: float | None = Field(default=None, ge=0)
    move_in_date: date | None = None
    desired_unit_type: str | None = Field(default=None, max_length=100)
    pet_info: dict[str, Any] | None = None
    number_of_occupants: int | None = Field(default=None, ge=1, le=20)
    reason_for_moving: str | None = Field(default=None, max_length=500)
    how_heard: str | None = Field(default=None, max_length=255)
    urgency: str | None = Field(default=None, max_length=50)
    tour_interest: bool = False
    lead_score: str | None = Field(default=None, max_length=20)
    lead_status: str = Field(default="new", max_length=50)


class LeadUpdateRequest(BaseModel):
    """Partial update — all fields optional."""

    name: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    email: str | None = Field(default=None, max_length=320)
    budget: float | None = Field(default=None, ge=0)
    move_in_date: date | None = None
    desired_unit_type: str | None = Field(default=None, max_length=100)
    pet_info: dict[str, Any] | None = None
    urgency: str | None = Field(default=None, max_length=50)
    tour_interest: bool | None = None
    lead_score: str | None = Field(default=None, max_length=20)
    lead_status: str | None = Field(default=None, max_length=50)


class LeadListItem(BaseModel):
    """Minimal lead data for the leads list view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    call_id: uuid.UUID | None
    name: str | None
    phone: str | None
    email: str | None
    lead_status: str
    lead_score: str | None
    tour_interest: bool
    urgency: str | None
    desired_unit_type: str | None
    created_at: datetime


class LeadDetailResponse(BaseModel):
    """Full lead record for the lead detail view."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    company_id: uuid.UUID
    call_id: uuid.UUID | None
    name: str | None
    phone: str | None
    email: str | None
    budget: float | None
    move_in_date: date | None
    desired_unit_type: str | None
    pet_info: dict[str, Any] | None
    number_of_occupants: int | None
    reason_for_moving: str | None
    how_heard: str | None
    urgency: str | None
    tour_interest: bool
    lead_score: str | None
    lead_status: str
    created_at: datetime
    updated_at: datetime
