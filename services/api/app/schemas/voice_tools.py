"""Pydantic v2 request and response schemas for all 10 voice tool endpoints.

These schemas are the contract between Harsha's backend and Akhil's voice agent.
Every field has an example value so the OpenAPI docs are self-explanatory.

NEEDS AKHIL REVIEW: CallEventType enum — confirm the full list of event types
  that the voice agent will emit before Phase 1 starts.
"""

import uuid
from datetime import date, time
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared sub-models
# ---------------------------------------------------------------------------


class DateRange(BaseModel):
    start_date: date
    end_date: date


class AvailableSlot(BaseModel):
    date: date
    start_time: time
    end_time: time
    # Opaque identifier returned by the calendar adapter — stable for the call session
    slot_id: str


class KnowledgeResult(BaseModel):
    chunk_text: str
    source_label: str
    page_number: int | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)


# ---------------------------------------------------------------------------
# 1. search_property_knowledge
# ---------------------------------------------------------------------------


class SearchKnowledgeRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "query": "What is the pet policy for large dogs?",
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "top_k": 5,
            }
        }
    )

    property_id: uuid.UUID
    query: str = Field(min_length=1, max_length=500)
    call_id: uuid.UUID
    top_k: int = Field(default=5, ge=1, le=20)


class SearchKnowledgeResponse(BaseModel):
    property_id: uuid.UUID
    query: str
    results: list[KnowledgeResult]


# ---------------------------------------------------------------------------
# 2. get_property_profile
# ---------------------------------------------------------------------------


class PropertyProfileResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "name": "Sunset Apartments",
                "address": "123 Sunset Blvd, Austin, TX 78701",
                "amenities": {"pool": True, "gym": True, "parking": "covered"},
                "office_hours": {"mon_fri": "9am-6pm", "sat": "10am-4pm"},
                "leasing_policies": "12-month minimum lease. First and last month required.",
                "maintenance_instructions": "Submit requests via the resident portal.",
                "escalation_contacts": [{"name": "Jane Smith", "phone": "+15125550100", "role": "Property Manager"}],
                "call_handling_rules": {"after_hours_message": "We are closed. Press 1 to leave a message."},
            }
        }
    )

    property_id: uuid.UUID
    name: str
    address: str | None
    description: str | None
    amenities: dict[str, Any] | None
    office_hours: dict[str, Any] | None
    leasing_policies: str | None
    maintenance_instructions: str | None
    escalation_contacts: list[dict[str, Any]] | None
    call_handling_rules: dict[str, Any] | None


# ---------------------------------------------------------------------------
# 3. create_or_update_lead
# ---------------------------------------------------------------------------


class LeadFieldsInput(BaseModel):
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
    urgency: Literal["low", "medium", "high", "immediate"] | None = None
    tour_interest: bool | None = None
    # hot | warm | cold — set by the voice agent based on conversation signals
    lead_score: Literal["hot", "warm", "cold"] | None = None


class CreateOrUpdateLeadRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "lead_fields": {
                    "name": "Maria Garcia",
                    "phone": "+15125550199",
                    "email": "maria@example.com",
                    "desired_unit_type": "2BR",
                    "move_in_date": "2026-07-01",
                    "budget": 2200.00,
                    "tour_interest": True,
                    "lead_score": "hot",
                },
            }
        }
    )

    property_id: uuid.UUID
    call_id: uuid.UUID
    lead_fields: LeadFieldsInput


class CreateOrUpdateLeadResponse(BaseModel):
    lead_id: uuid.UUID
    property_id: uuid.UUID
    call_id: uuid.UUID
    # True if a new lead record was created; False if an existing one was updated
    created: bool


# ---------------------------------------------------------------------------
# 4. create_call_event
# ---------------------------------------------------------------------------


# NEEDS AKHIL REVIEW: confirm this list matches every event the voice agent emits
class CallEventType(str, Enum):
    call_started = "call_started"
    call_ended = "call_ended"
    lead_captured = "lead_captured"
    tour_booked = "tour_booked"
    email_sent = "email_sent"
    escalated = "escalated"
    knowledge_retrieved = "knowledge_retrieved"
    tool_called = "tool_called"
    tool_failed = "tool_failed"
    interruption_detected = "interruption_detected"
    silence_detected = "silence_detected"


class CreateCallEventRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "event_type": "lead_captured",
                "payload": {"lead_id": "a1b2c3d4-0000-0000-0000-000000000003"},
                "occurred_at": "2026-05-09T14:32:00Z",
            }
        }
    )

    call_id: uuid.UUID
    event_type: CallEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: str = Field(description="ISO 8601 timestamp")


class CreateCallEventResponse(BaseModel):
    event_id: uuid.UUID
    call_id: uuid.UUID


# ---------------------------------------------------------------------------
# 5. save_transcript_segment
# ---------------------------------------------------------------------------


class SaveTranscriptSegmentRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "speaker": "caller",
                "text": "Hi, I was wondering about your 2-bedroom availability.",
                "timestamp": 12.34,
            }
        }
    )

    call_id: uuid.UUID
    speaker: Literal["agent", "caller"]
    text: str = Field(min_length=1, max_length=5000)
    # Seconds since call start — from Whisper/LiveKit
    timestamp: float = Field(ge=0.0)


class SaveTranscriptSegmentResponse(BaseModel):
    segment_id: uuid.UUID
    call_id: uuid.UUID


# ---------------------------------------------------------------------------
# 6. save_call_summary
# ---------------------------------------------------------------------------


class SaveCallSummaryRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "summary": "Maria called to ask about 2-bedroom availability. She is looking to move in July and has a budget of $2,200/month. A tour was booked for May 15th.",
                "primary_intent": "leasing_inquiry",
                "sentiment": "positive",
                "action_items": ["[TOUR] Confirm tour booking for May 15"],
                "escalation_flag": False,
                "lead_fields_extracted": {"name": "Maria Garcia", "move_in_date": "2026-07-01"},
                "next_steps": "Send tour confirmation email.",
            }
        }
    )

    call_id: uuid.UUID
    summary: str = Field(min_length=1, max_length=5000)
    primary_intent: str = Field(max_length=100)
    sentiment: Literal["positive", "neutral", "negative", "frustrated"] = "neutral"
    action_items: list[str] = Field(default_factory=list)
    escalation_flag: bool = False
    lead_fields_extracted: dict[str, Any] = Field(default_factory=dict)
    next_steps: str | None = Field(default=None, max_length=1000)


class SaveCallSummaryResponse(BaseModel):
    call_id: uuid.UUID
    summary_saved: bool


# ---------------------------------------------------------------------------
# 7. check_tour_availability
# ---------------------------------------------------------------------------


class CheckTourAvailabilityRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "date_range": {"start_date": "2026-05-12", "end_date": "2026-05-16"},
            }
        }
    )

    property_id: uuid.UUID
    date_range: DateRange


class CheckTourAvailabilityResponse(BaseModel):
    property_id: uuid.UUID
    available_slots: list[AvailableSlot]


# ---------------------------------------------------------------------------
# 8. book_tour
# ---------------------------------------------------------------------------


class BookingSlotInput(BaseModel):
    date: date
    start_time: time
    end_time: time
    slot_id: str


class BookTourRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "lead_id": "a1b2c3d4-0000-0000-0000-000000000003",
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "selected_slot": {
                    "date": "2026-05-15",
                    "start_time": "10:00:00",
                    "end_time": "10:30:00",
                    "slot_id": "gcal-slot-abc123",
                },
                "tour_type": "in_person",
            }
        }
    )

    property_id: uuid.UUID
    lead_id: uuid.UUID
    call_id: uuid.UUID
    selected_slot: BookingSlotInput
    tour_type: Literal["in_person", "self_guided", "virtual"] = "in_person"


class BookTourResponse(BaseModel):
    booking_id: uuid.UUID
    calendar_event_id: str | None
    tour_date: date
    start_time: time
    status: str


# ---------------------------------------------------------------------------
# 9. send_follow_up_email
# ---------------------------------------------------------------------------


class EmailTemplateType(str, Enum):
    tour_confirmation = "tour_confirmation"
    follow_up = "follow_up"
    lead_response = "lead_response"
    general = "general"


class SendFollowUpEmailRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "lead_id": "a1b2c3d4-0000-0000-0000-000000000003",
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "template_type": "tour_confirmation",
                "context": {
                    "lead_name": "Maria Garcia",
                    "tour_date": "May 15, 2026",
                    "tour_time": "10:00 AM",
                    "property_name": "Sunset Apartments",
                    "property_address": "123 Sunset Blvd, Austin, TX 78701",
                },
            }
        }
    )

    property_id: uuid.UUID
    lead_id: uuid.UUID
    call_id: uuid.UUID
    template_type: EmailTemplateType
    # Template variable values — must match the template's placeholder names
    context: dict[str, Any] = Field(default_factory=dict)


class SendFollowUpEmailResponse(BaseModel):
    email_id: uuid.UUID
    recipient: str
    subject: str
    delivery_status: str


# ---------------------------------------------------------------------------
# 10. request_human_handoff
# ---------------------------------------------------------------------------


class HandoffUrgency(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    emergency = "emergency"


class RequestHandoffRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "property_id": "a1b2c3d4-0000-0000-0000-000000000001",
                "call_id": "a1b2c3d4-0000-0000-0000-000000000002",
                "reason": "Caller mentioned attorney involvement and is threatening legal action.",
                "urgency": "high",
                "lead_id": "a1b2c3d4-0000-0000-0000-000000000003",
            }
        }
    )

    property_id: uuid.UUID
    call_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=1000)
    urgency: HandoffUrgency
    lead_id: uuid.UUID | None = None


class RequestHandoffResponse(BaseModel):
    handoff_id: uuid.UUID
    call_id: uuid.UUID
    status: str
    notification_sent: bool
