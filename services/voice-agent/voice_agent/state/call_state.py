"""
Per-call state model.

One CallState instance lives for the duration of a single phone call.
It is held in memory by the LiveKit Agent worker (Phase 1) and never
written directly to the database — durable persistence happens through
backend tool calls (save_transcript_segment, save_call_summary, etc.).

Design notes:
- All fields are Optional where we don't know the value yet at call start.
- LeadFields is kept separate so it maps cleanly to create_or_update_lead.
- TranscriptSegment maps to save_transcript_segment.
- confidence_score tracks how certain the agent is about the last response.
  Scores below ESCALATION_CONFIDENCE_THRESHOLD should trigger escalation.
- escalation_flag + escalation_reason are set by the agent and sent to
  request_human_handoff before ending the call.

Field alignment:
  LeadFields field names match services/api/app/schemas/voice_tools.py::LeadFieldsInput.
  summary_payload() output matches services/api/app/schemas/voice_tools.py::SaveCallSummaryRequest.
  TourSlot maps to BookingSlotInput / AvailableSlot in voice_tools.py.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CallPhase(str, Enum):
    """
    Conversation state machine phases.

    Transitions (happy path):
      GREETING -> INTENT_DETECTION -> QUALIFICATION -> (KNOWLEDGE_RETRIEVAL) ->
      TOUR_BOOKING | RESIDENT_SUPPORT -> CLOSING -> ENDED

    Any phase can jump to ESCALATION or ENDED on error / handoff trigger.
    """

    GREETING = "greeting"
    INTENT_DETECTION = "intent_detection"
    QUALIFICATION = "qualification"              # lead capture for prospects
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"  # RAG lookup in progress (Phase 3)
    TOUR_BOOKING = "tour_booking"                # availability + confirm + book (Phase 4)
    RESIDENT_SUPPORT = "resident_support"        # maintenance / resident Q&A
    CLOSING = "closing"                          # wrap-up, send follow-up prompt
    ESCALATION = "escalation"                    # handing off to human
    ENDED = "ended"                              # call complete


class CallerIntent(str, Enum):
    """
    Detected primary intent for the call.
    Detected during INTENT_DETECTION phase, refined through the call.
    """

    UNKNOWN = "unknown"
    LEASING_INQUIRY = "leasing_inquiry"          # prospect asking about units/pricing
    TOUR_REQUEST = "tour_request"                # explicitly wants to schedule a tour
    MAINTENANCE = "maintenance"                  # resident reporting a maintenance issue
    RESIDENT_SUPPORT = "resident_support"        # general resident question
    ESCALATION_REQUESTED = "escalation_requested"  # caller asking for a human


class SpeakerRole(str, Enum):
    """Who spoke a transcript segment."""

    AGENT = "agent"
    CALLER = "caller"


class EscalationReason(str, Enum):
    """
    Why the call is being escalated to a human.
    Sent as the 'reason' string in request_human_handoff payload.
    (Harsha's endpoint accepts free-text reason, not an enum — we keep
    this enum internally for consistency; .value is passed to the tool.)

    Use to_handoff_urgency() to derive the HandoffUrgency required by
    Harsha's request_human_handoff endpoint. Never pass urgency manually
    in VoiceSession — always derive it from the EscalationReason.
    """

    FAIR_HOUSING_QUESTION = "fair_housing_question"
    LEGAL_QUESTION = "legal_question"
    FINANCIAL_ADVICE_REQUESTED = "financial_advice_requested"
    ELIGIBILITY_QUESTION = "eligibility_question"
    EMERGENCY = "emergency"
    LOW_CONFIDENCE = "low_confidence"
    CALLER_REQUESTED = "caller_requested"
    BACKEND_TOOL_FAILURE = "backend_tool_failure"
    BOOKING_FAILED = "booking_failed"
    UNKNOWN = "unknown"

    def to_handoff_urgency(self) -> "HandoffUrgency":
        """
        Map internal EscalationReason to Harsha's HandoffUrgency enum.

        Mapping (OQ-13 from voice-tools.akhil-draft.md):
          EMERGENCY                -> emergency
          FAIR_HOUSING_QUESTION    -> high
          LEGAL_QUESTION           -> high
          FINANCIAL_ADVICE_REQUESTED -> high
          ELIGIBILITY_QUESTION     -> high
          CALLER_REQUESTED         -> high   (explicit human request — respect it)
          LOW_CONFIDENCE           -> medium
          BACKEND_TOOL_FAILURE     -> medium
          BOOKING_FAILED           -> medium
          UNKNOWN                  -> medium  (conservative default)
        """
        return _ESCALATION_URGENCY_MAP.get(self, HandoffUrgency.MEDIUM)


class HandoffUrgency(str, Enum):
    """
    Mirrors services/api/app/schemas/voice_tools.py::HandoffUrgency.
    Required field in POST /v1/voice/request-handoff.
    Derive from EscalationReason.to_handoff_urgency() — never set manually.
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    EMERGENCY = "emergency"


# Mapping used by EscalationReason.to_handoff_urgency().
# Defined after both enums so forward references resolve cleanly.
_ESCALATION_URGENCY_MAP: dict["EscalationReason", HandoffUrgency] = {
    EscalationReason.EMERGENCY: HandoffUrgency.EMERGENCY,
    EscalationReason.FAIR_HOUSING_QUESTION: HandoffUrgency.HIGH,
    EscalationReason.LEGAL_QUESTION: HandoffUrgency.HIGH,
    EscalationReason.FINANCIAL_ADVICE_REQUESTED: HandoffUrgency.HIGH,
    EscalationReason.ELIGIBILITY_QUESTION: HandoffUrgency.HIGH,
    EscalationReason.CALLER_REQUESTED: HandoffUrgency.HIGH,
    EscalationReason.LOW_CONFIDENCE: HandoffUrgency.MEDIUM,
    EscalationReason.BACKEND_TOOL_FAILURE: HandoffUrgency.MEDIUM,
    EscalationReason.BOOKING_FAILED: HandoffUrgency.MEDIUM,
    EscalationReason.UNKNOWN: HandoffUrgency.MEDIUM,
}


# ---------------------------------------------------------------------------
# Confidence threshold
# ---------------------------------------------------------------------------

# If agent confidence drops below this, trigger escalation path.
# Tune during Phase 5 based on real call data.
ESCALATION_CONFIDENCE_THRESHOLD = 0.55


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class TranscriptSegment(BaseModel):
    """
    One turn of speech — maps directly to save_transcript_segment tool payload.

    Fields kept minimal to match backend contract (docs/contracts/voice-tools.md).
    call_id and timestamp are set when the segment is flushed to the backend.

    NOTE: timestamp is float seconds-since-call-start (Harsha's schema), not
    an ISO datetime string. The 'created_at' datetime is local bookkeeping only.
    """

    segment_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    speaker: SpeakerRole
    text: str = Field(min_length=1)
    # Seconds since call start — set from LiveKit/Whisper offset at flush time.
    # None until the segment is ready to flush.
    timestamp_offset_seconds: float | None = None
    # Wall-clock time for local bookkeeping (not sent to backend).
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # True once this segment has been successfully POSTed to the backend.
    # Prevents double-posting on retry.
    flushed_to_backend: bool = False

    @field_validator("text")
    @classmethod
    def strip_text(cls, v: str) -> str:
        return v.strip()


class LeadFields(BaseModel):
    """
    Lead capture fields accumulated during the call.

    Maps to LeadFieldsInput in services/api/app/schemas/voice_tools.py,
    which is the 'lead_fields' argument of create_or_update_lead.

    Field name alignment (Akhil draft -> Harsha real schema):
      phone_number        -> phone
      desired_move_in_date -> move_in_date   (date object, not str)
      budget_min / budget_max -> budget      (single float)
      occupants           -> number_of_occupants
      pet_info str        -> pet_info dict   (structured, e.g. {"type": "dog", "weight_lbs": 45})
      urgency "asap"/"this_month"/"flexible" -> "low"/"medium"/"high"/"immediate"
      [removed]           preferred_contact_method (not in Harsha's schema)
      [added]             reason_for_moving, how_heard, lead_score

    email_confirmed is NOT sent to the backend — it is local call state only.
    It must be True before send_follow_up_email is called.

    All fields are optional — send only what the caller explicitly stated.
    Never invent values.
    """

    name: str | None = None
    phone: str | None = None                        # was: phone_number
    email: str | None = None
    budget: float | None = None                     # was: budget_min + budget_max (separate)
    move_in_date: date | None = None                # was: desired_move_in_date (str)
    desired_unit_type: str | None = None            # e.g. "1BR", "2BR", "studio"
    pet_info: dict[str, Any] | None = None          # was: str; now structured dict
    number_of_occupants: int | None = None          # was: occupants
    reason_for_moving: str | None = None            # new field
    how_heard: str | None = None                    # new field
    urgency: Literal["low", "medium", "high", "immediate"] | None = None
    tour_interest: bool | None = None
    lead_score: Literal["hot", "warm", "cold"] | None = None  # new field

    # email_confirmed: True only after agent has read back the email address
    # and caller confirmed it. Required before send_follow_up_email.
    # This field is NOT included in model_dump() calls to the backend.
    email_confirmed: bool = False

    def to_backend_payload(self) -> dict[str, Any]:
        """
        Produce the dict to send as 'lead_fields' to create_or_update_lead.
        Excludes email_confirmed (local-only) and None values.
        """
        return self.model_dump(
            mode="json",
            exclude={"email_confirmed"},
            exclude_none=True,
        )


class TourSlot(BaseModel):
    """
    A tour slot — maps to AvailableSlot (from check_tour_availability)
    and BookingSlotInput (sent to book_tour).

    Changed from Akhil draft:
      - Added end_time (required by BookingSlotInput)
      - start_time is datetime.time, not a string like "10:00 AM"
      - Removed: timezone, duration_minutes (not in Harsha's slot shapes)
      - date is datetime.date, not a string
    """

    slot_id: str
    date: date
    start_time: time
    end_time: time


# ---------------------------------------------------------------------------
# Main call state
# ---------------------------------------------------------------------------


class CallState(BaseModel):
    """
    Complete per-call state.

    Instantiated at call start (GREETING phase).
    Updated in-place as the conversation progresses.
    Serialized into save_call_summary payload at call end.

    PII handling: this object lives in memory only for the call duration.
    Do not write it to logs. Flush PII (email, phone, name) only via
    backend tool calls which enforce property-scoped storage.
    """

    # ------------------------------------------------------------------
    # Identity — set at call start
    # ------------------------------------------------------------------
    call_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Local call UUID generated at instantiation for internal correlation.",
    )
    backend_call_id: str | None = Field(
        default=None,
        description=(
            "UUID returned by POST /v1/calls/ — the authoritative ID used by all "
            "backend tool calls. Set by VoiceSession.start() after create_call() "
            "succeeds. None until start() completes."
        ),
    )
    property_id: str = Field(
        description="Which property this call is for. Required to scope all tool calls.",
    )
    twilio_call_sid: str | None = Field(
        default=None,
        description="Set by Twilio webhook in Phase 1.",
    )
    livekit_room_id: str | None = Field(
        default=None,
        description="Set when LiveKit room is created in Phase 1.",
    )
    caller_phone_number: str | None = Field(
        default=None,
        description="From Twilio caller ID. Treat as PII — do not log.",
    )

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None

    # ------------------------------------------------------------------
    # Conversation state machine
    # ------------------------------------------------------------------
    phase: CallPhase = CallPhase.GREETING
    intent: CallerIntent = CallerIntent.UNKNOWN

    # ------------------------------------------------------------------
    # Confidence and escalation
    # ------------------------------------------------------------------
    confidence_score: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Agent's confidence in its last response. "
            "Updated after each LLM turn. "
            f"Values below {ESCALATION_CONFIDENCE_THRESHOLD} trigger escalation."
        ),
    )
    escalation_flag: bool = False
    escalation_reason: EscalationReason | None = None
    escalation_notes: str | None = Field(
        default=None,
        description="Free-text context for the human receiving the handoff.",
    )

    # ------------------------------------------------------------------
    # Lead capture (prospect calls)
    # ------------------------------------------------------------------
    lead_id: str | None = Field(
        default=None,
        description="Set by backend after first create_or_update_lead call.",
    )
    lead_fields: LeadFields = Field(default_factory=LeadFields)

    # ------------------------------------------------------------------
    # Tour booking (Phase 4)
    # ------------------------------------------------------------------
    available_tour_slots: list[TourSlot] = Field(default_factory=list)
    selected_tour_slot: TourSlot | None = None
    booking_confirmed: bool = False
    booking_id: str | None = None

    # ------------------------------------------------------------------
    # Transcript
    # ------------------------------------------------------------------
    transcript: list[TranscriptSegment] = Field(default_factory=list)

    # ------------------------------------------------------------------
    # RAG context (Phase 3)
    # ------------------------------------------------------------------
    last_retrieved_chunks: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Most recent RAG results. Used to ground the current Gemini prompt.",
    )

    # ------------------------------------------------------------------
    # Follow-up
    # ------------------------------------------------------------------
    follow_up_email_sent: bool = False

    # ------------------------------------------------------------------
    # Internal retry tracking
    # ------------------------------------------------------------------
    tool_failure_count: int = Field(
        default=0,
        description="Cumulative count of backend tool failures this call.",
    )

    # ------------------------------------------------------------------
    # LLM-generated summary (populated during CLOSING phase by Gemini)
    # ------------------------------------------------------------------
    ai_summary: str | None = Field(
        default=None,
        description=(
            "Gemini-generated plain-text summary of the call. "
            "Populated during CLOSING phase before save_call_summary is called."
        ),
    )
    ai_action_items: list[str] = Field(
        default_factory=list,
        description="Action items extracted by Gemini during CLOSING phase.",
    )
    ai_next_steps: str | None = Field(
        default=None,
        description="Next-steps text generated by Gemini during CLOSING phase.",
    )
    sentiment: Literal["positive", "neutral", "negative", "frustrated"] = "neutral"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def add_segment(self, speaker: SpeakerRole, text: str) -> TranscriptSegment:
        """Append a transcript segment and return it."""
        seg = TranscriptSegment(speaker=speaker, text=text)
        self.transcript.append(seg)
        return seg

    def set_phase(self, phase: CallPhase) -> None:
        """Advance the conversation state machine."""
        self.phase = phase

    def trigger_escalation(
        self,
        reason: EscalationReason,
        notes: str | None = None,
    ) -> None:
        """
        Mark the call for human handoff.

        After calling this, the agent should call request_human_handoff
        via the backend tool, then move to ESCALATION phase and end the call.
        """
        self.escalation_flag = True
        self.escalation_reason = reason
        self.escalation_notes = notes
        self.phase = CallPhase.ESCALATION

    def mark_ended(self) -> None:
        self.ended_at = datetime.now(timezone.utc)
        self.phase = CallPhase.ENDED

    @property
    def duration_seconds(self) -> float | None:
        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds()

    @property
    def unflushed_segments(self) -> list[TranscriptSegment]:
        """Transcript segments not yet persisted to the backend."""
        return [s for s in self.transcript if not s.flushed_to_backend]

    @property
    def should_escalate_on_confidence(self) -> bool:
        return self.confidence_score < ESCALATION_CONFIDENCE_THRESHOLD

    def summary_payload(self) -> dict[str, Any]:
        """
        Produce the payload for save_call_summary.

        Output matches services/api/app/schemas/voice_tools.py::SaveCallSummaryRequest:
          call_id, summary, primary_intent, sentiment, action_items,
          escalation_flag, lead_fields_extracted, next_steps

        Fields NOT in Harsha's schema (omitted vs. Akhil draft):
          property_id, booking_confirmed, booking_id, follow_up_email_sent,
          duration_seconds, confidence_score_final, tool_failure_count,
          escalation_reason.

        The 'summary' field is generated by Gemini in the CLOSING phase and
        stored in self.ai_summary. If Gemini has not yet populated it (e.g.
        call ended unexpectedly), a fallback is used.
        """
        summary_text = self.ai_summary or (
            f"Call ended in phase {self.phase.value}. "
            f"Intent: {self.intent.value}. "
            f"Escalated: {self.escalation_flag}."
        )
        return {
            "call_id": self.call_id,
            "summary": summary_text,
            "primary_intent": self.intent.value,
            "sentiment": self.sentiment,
            "action_items": list(self.ai_action_items),
            "escalation_flag": self.escalation_flag,
            "lead_fields_extracted": self.lead_fields.to_backend_payload(),
            "next_steps": self.ai_next_steps,
        }
