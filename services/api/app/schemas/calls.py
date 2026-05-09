"""Pydantic schemas for Call endpoints — consumed by Alex (dashboard) and Akhil (voice tools)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TranscriptSegment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    call_id: uuid.UUID
    speaker: str  # "agent" | "caller"
    text: str
    timestamp: float
    created_at: datetime


class CallEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    call_id: uuid.UUID
    event_type: str
    payload: dict[str, Any]
    occurred_at: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Create / Update
# ---------------------------------------------------------------------------


class CallCreateRequest(BaseModel):
    """Sent by Akhil's voice agent when a new inbound call is received."""

    property_id: uuid.UUID
    twilio_call_sid: str = Field(max_length=64)
    livekit_room_id: str | None = Field(default=None, max_length=255)
    caller_phone: str | None = Field(default=None, max_length=50)
    started_at: str | None = Field(default=None, description="ISO 8601 timestamp")


class CallUpdateRequest(BaseModel):
    """Sent by Akhil when the call ends or status changes."""

    ended_at: str | None = Field(default=None, description="ISO 8601 timestamp")
    duration: int | None = Field(default=None, ge=0, description="Call duration in seconds")
    status: str | None = Field(default=None, max_length=50)
    primary_intent: str | None = Field(default=None, max_length=100)
    sentiment: str | None = Field(default=None, max_length=50)
    escalation_status: str | None = Field(default=None, max_length=50)
    escalation_flag: bool | None = None


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class CallListItem(BaseModel):
    """Minimal call data for list views — no transcript text to keep payloads small."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    caller_phone: str | None
    started_at: str | None
    ended_at: str | None
    duration: int | None
    status: str
    primary_intent: str | None
    sentiment: str | None
    escalation_status: str
    escalation_flag: bool
    created_at: datetime


class CallDetailResponse(BaseModel):
    """Full call record for the call detail page."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    company_id: uuid.UUID
    twilio_call_sid: str | None
    livekit_room_id: str | None
    caller_phone: str | None
    started_at: str | None
    ended_at: str | None
    duration: int | None
    status: str
    primary_intent: str | None
    sentiment: str | None
    escalation_status: str
    escalation_flag: bool
    # Null until save_call_summary is called
    summary: str | None
    action_items: list[str] | None
    next_steps: str | None
    lead_fields_extracted: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    # Populated from related tables in the route handler
    transcript_segments: list[TranscriptSegment] = Field(default_factory=list)
    call_events: list[CallEvent] = Field(default_factory=list)
    # Null until leads are linked in Phase 2
    lead_id: uuid.UUID | None = None


class CallCreateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    property_id: uuid.UUID
    status: str
    created_at: datetime
