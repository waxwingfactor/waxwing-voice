"""Pydantic schemas for Property endpoints — consumed by Alex (dashboard) and Akhil (get_property_profile)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class PropertyListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    address: str | None
    created_at: datetime


class PropertyDetailResponse(BaseModel):
    """Full property profile — returned by GET /v1/properties/{id} and get_property_profile tool."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    name: str
    address: str | None
    description: str | None
    amenities: dict[str, Any] | None
    office_hours: dict[str, Any] | None
    leasing_policies: str | None
    maintenance_instructions: str | None
    escalation_contacts: list[dict[str, Any]] | None
    business_hour_rules: dict[str, Any] | None
    call_handling_rules: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class PropertyUpdateRequest(BaseModel):
    """Partial update payload for PATCH /v1/properties/{id}.

    All fields are Optional — only fields the client sends are applied.
    Use ``model_dump(exclude_unset=True)`` to determine which fields the
    client explicitly set (vs. left as default None).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    address: str | None = None
    description: str | None = None
    amenities: dict[str, Any] | None = None
    office_hours: dict[str, Any] | None = None
    leasing_policies: str | None = None
    maintenance_instructions: str | None = None
    escalation_contacts: list[dict[str, Any]] | None = None
    business_hour_rules: dict[str, Any] | None = None
    call_handling_rules: dict[str, Any] | None = None


class PropertySummaryResponse(BaseModel):
    """Aggregated metrics for the home dashboard tiles.

    NEEDS ALEX REVIEW: confirm which metrics are needed for the dashboard cards.
    """

    property_id: uuid.UUID
    property_name: str
    date: str  # YYYY-MM-DD — the window for these metrics

    calls_today: int
    new_leads_today: int
    tours_booked_today: int
    escalations_today: int
    follow_ups_sent_today: int
    open_action_items: int
