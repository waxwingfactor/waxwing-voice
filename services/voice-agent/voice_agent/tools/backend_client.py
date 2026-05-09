"""
Backend tool client.

Wraps all calls to Harsha's FastAPI endpoints. This is the ONLY place in
the voice agent that makes HTTP calls to the backend. It enforces:
- Correct URL construction per services/api/app/api/ routes (authoritative)
- Module-level path constants so any future path change is a one-line update
- Timeout discipline (per per-tool SLO table in voice-tools.md)
- Structured error handling with retryable/non-retryable distinction
- PII-safe logging (no caller phone, email, or name in log lines)

Source of truth for all request/response shapes:
    services/api/app/schemas/voice_tools.py
    services/api/app/schemas/calls.py

Auth: Bearer JWT (Phase 5+). The token is an HS256-signed JWT produced by
      services/api/app/auth.py::create_access_token(). It contains a
      {"company_id": "<uuid>", "exp": <timestamp>} payload and is signed with
      SECRET_KEY from Harsha's backend settings. The voice agent receives this
      token as the VOICE_AGENT_JWT env var (provisioned by Subbu). It is sent
      on EVERY request via the Authorization: Bearer <token> header, set both
      as client default headers and repeated per-request to ensure it is never
      accidentally dropped.

NOTE on call_id types: CallState.call_id is a plain str (UUID4 string).
BackendClient methods accept uuid.UUID for type safety on the API boundary.
Callers should pass uuid.UUID(state.call_id).

TODO: Once packages/shared exists, extract the mirrored Pydantic types below
      (VoiceToolSchemas.*) into that package so both services import from a
      single source rather than maintaining parallel copies.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, time
from enum import Enum
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

log = logging.getLogger("voice_agent.tools.backend_client")

# ---------------------------------------------------------------------------
# Module-level path constants
# One-line update if Harsha changes a route.
# ---------------------------------------------------------------------------

# Call lifecycle (calls.py)
_PATH_POST_CALL = "/v1/calls/"

# Voice tools (voice.py)
_PATH_SEARCH_KNOWLEDGE = "/v1/voice/search-knowledge"
_PATH_POST_LEAD = "/v1/voice/leads"
_PATH_POST_EVENT = "/v1/voice/events"
_PATH_POST_TRANSCRIPT = "/v1/voice/transcript-segment"
_PATH_POST_CALL_SUMMARY = "/v1/voice/call-summary"
_PATH_CHECK_AVAILABILITY = "/v1/voice/check-availability"
_PATH_BOOK_TOUR = "/v1/voice/book-tour"
_PATH_SEND_EMAIL = "/v1/voice/send-email"
_PATH_REQUEST_HANDOFF = "/v1/voice/request-handoff"

# Property profile (properties.py) — parametric, built at call time
def _path_get_property(property_id: uuid.UUID) -> str:
    return f"/v1/properties/{property_id}"


# ---------------------------------------------------------------------------
# Mirrored Pydantic types — kept in sync with services/api/app/schemas/voice_tools.py
# TODO: extract to packages/shared once that package exists (flag for Subbu).
# ---------------------------------------------------------------------------


class DateRange(BaseModel):
    start_date: date
    end_date: date


class AvailableSlot(BaseModel):
    date: date
    start_time: time
    end_time: time
    slot_id: str  # opaque; stable for the call session


class KnowledgeResult(BaseModel):
    chunk_text: str
    source_label: str
    page_number: int | None = None
    similarity_score: float = Field(ge=0.0, le=1.0)


class LeadFieldsInput(BaseModel):
    """
    Mirrors services/api/app/schemas/voice_tools.py::LeadFieldsInput.

    IMPORTANT field-name alignment notes (Akhil draft -> Harsha real):
      phone_number       -> phone
      desired_move_in_date -> move_in_date  (date, not str)
      budget_min / budget_max -> budget    (single float)
      occupants          -> number_of_occupants
      pet_info           -> pet_info       (dict, not free-text str)
      urgency            -> urgency        ("low"|"medium"|"high"|"immediate", not "asap"/"this_month")
      [removed]          preferred_contact_method  (not in Harsha's schema)
      [added]            reason_for_moving, how_heard, lead_score
      [moved out]        email_confirmed   (stays on CallState only — not sent to backend)
    """

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
    lead_score: Literal["hot", "warm", "cold"] | None = None


class BookingSlotInput(BaseModel):
    date: date
    start_time: time
    end_time: time
    slot_id: str


class CallEventType(str, Enum):
    """
    Mirrors services/api/app/schemas/voice_tools.py::CallEventType.

    Locked as final for Phase 1. Harsha's additions kept:
      knowledge_retrieved, tool_called, interruption_detected, silence_detected.
    Akhil's intent_detected, phase_changed, lead_field_captured fold into
    save_call_summary payload rather than separate events.
    """

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


class EmailTemplateType(str, Enum):
    """
    Mirrors services/api/app/schemas/voice_tools.py::EmailTemplateType.

    Akhil draft had: tour_confirmation, general_followup, maintenance_acknowledgment
    Harsha's real:   tour_confirmation, follow_up, lead_response, general
    Removed: general_followup -> use follow_up or general
    Removed: maintenance_acknowledgment -> not in schema (flow ends at handoff)
    """

    tour_confirmation = "tour_confirmation"
    follow_up = "follow_up"
    lead_response = "lead_response"
    general = "general"


class HandoffUrgency(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    emergency = "emergency"


# ---------------------------------------------------------------------------
# Response mirrors
# ---------------------------------------------------------------------------


class CallCreateResponse(BaseModel):
    """
    Mirrors services/api/app/schemas/calls.py::CallCreateResponse.
    Returned by POST /v1/calls/ — the call_id used by all subsequent tool calls.
    """
    id: uuid.UUID
    property_id: uuid.UUID
    status: str


class SearchKnowledgeResponse(BaseModel):
    property_id: uuid.UUID
    query: str
    results: list[KnowledgeResult]


class PropertyProfileResponse(BaseModel):
    """
    Mirrors services/api/app/schemas/voice_tools.py::PropertyProfileResponse.

    Wire field is 'id' (resolved in Harsha commit 581d6f3a — voice_tools.py
    PropertyProfileResponse renamed property_id -> id to match
    PropertyDetailResponse in properties.py).
    """

    id: uuid.UUID
    name: str
    address: str | None
    description: str | None
    amenities: dict[str, Any] | None
    office_hours: dict[str, Any] | None
    leasing_policies: str | None
    maintenance_instructions: str | None
    escalation_contacts: list[dict[str, Any]] | None
    call_handling_rules: dict[str, Any] | None


class CreateOrUpdateLeadResponse(BaseModel):
    lead_id: uuid.UUID
    property_id: uuid.UUID
    call_id: uuid.UUID
    created: bool  # True = new record; False = existing record updated


class CreateCallEventResponse(BaseModel):
    event_id: uuid.UUID
    call_id: uuid.UUID


class SaveTranscriptSegmentResponse(BaseModel):
    segment_id: uuid.UUID
    call_id: uuid.UUID


class SaveCallSummaryResponse(BaseModel):
    call_id: uuid.UUID
    summary_saved: bool


class CheckTourAvailabilityResponse(BaseModel):
    property_id: uuid.UUID
    available_slots: list[AvailableSlot]


class BookTourResponse(BaseModel):
    booking_id: uuid.UUID
    calendar_event_id: str | None
    tour_date: date
    start_time: time
    status: str


class SendFollowUpEmailResponse(BaseModel):
    email_id: uuid.UUID
    recipient: str
    subject: str
    delivery_status: str


class RequestHandoffResponse(BaseModel):
    handoff_id: uuid.UUID
    call_id: uuid.UUID
    status: str
    notification_sent: bool


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class BackendToolError(Exception):
    """
    Raised when a backend tool call fails.

    Attributes:
        code:               Error code from backend (e.g. "PROPERTY_NOT_FOUND").
                            See services/api/app/schemas/errors.py for stable codes.
                            "RATE_LIMITED" is set when the backend returns 429.
        retryable:          True if the voice agent may retry after a short wait.
        message:            Human-readable description (safe to log).
        status_code:        HTTP status code received (None if no HTTP response).
        retry_after_seconds: Populated from the Retry-After response header when
                            code is "RATE_LIMITED". None if the header was absent.
                            The retry logic should sleep this many seconds before
                            the second attempt; if None, retry immediately.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        status_code: int | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.message = message
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds

    def __repr__(self) -> str:
        return (
            f"BackendToolError(code={self.code!r}, "
            f"retryable={self.retryable}, "
            f"status_code={self.status_code}, "
            f"retry_after_seconds={self.retry_after_seconds})"
        )


def _parse_error(response: httpx.Response) -> BackendToolError:
    """
    Parse the standard error envelope from Harsha's backend.

    Expected shape: {"error": {"code": "...", "message": "...", "retryable": bool}}
    Falls back to a generic error if the body is not in that shape.

    429 Rate Limit: returns BackendToolError with code="RATE_LIMITED",
    retryable=True, and retry_after_seconds parsed from the Retry-After header
    (None if the header is absent).
    """
    if response.status_code == 429:
        retry_after: int | None = None
        raw_header = response.headers.get("retry-after")
        if raw_header is not None:
            try:
                retry_after = int(raw_header)
            except (ValueError, TypeError):
                retry_after = None
        return BackendToolError(
            code="RATE_LIMITED",
            message=f"Rate limit exceeded (429). Retry-After: {retry_after}s",
            retryable=True,
            status_code=429,
            retry_after_seconds=retry_after,
        )
    try:
        body = response.json()
        err = body["error"]
        return BackendToolError(
            code=err["code"],
            message=err.get("message", "Backend error"),
            retryable=bool(err.get("retryable", False)),
            status_code=response.status_code,
        )
    except Exception:
        return BackendToolError(
            code="UNEXPECTED_RESPONSE",
            message=f"HTTP {response.status_code}: {response.text[:200]}",
            retryable=response.status_code >= 500,
            status_code=response.status_code,
        )


# ---------------------------------------------------------------------------
# Timeout constants (per voice-tools.md SLO table)
# ---------------------------------------------------------------------------

_TIMEOUT_CREATE_CALL = 5.0        # session bootstrap write
_TIMEOUT_PROPERTY_PROFILE = 0.5   # GET cached data; fast
_TIMEOUT_SEARCH_KNOWLEDGE = 2.0   # vector search; 2s cap per contract
_TIMEOUT_LEAD = 5.0               # database write
_TIMEOUT_CALL_EVENT = 5.0         # fire-and-forget write
_TIMEOUT_TRANSCRIPT = 5.0         # high-frequency write
_TIMEOUT_CALL_SUMMARY = 5.0       # end-of-call write
_TIMEOUT_CHECK_AVAILABILITY = 3.0  # calendar read; 3s per contract
_TIMEOUT_BOOK_TOUR = 5.0          # calendar write
_TIMEOUT_SEND_EMAIL = 5.0         # async ok
_TIMEOUT_HANDOFF = 5.0            # must not block caller disconnect


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class BackendClient:
    """
    Async HTTP client for Harsha's voice tool endpoints.

    All 10 voice tools plus create_call are implemented as async methods.
    Each method:
      - Accepts typed arguments matching Harsha's Pydantic request schema
      - Returns a typed Pydantic response model
      - Raises BackendToolError on any non-2xx response

    Auth: Bearer JWT (Phase 5+). The Authorization header is set both in the
    default client headers and repeated per-request so it is never dropped by
    header merging. The JWT is provisioned by Subbu via the VOICE_AGENT_JWT
    env var (see config.py). It is signed with the backend SECRET_KEY and
    carries a company_id claim. Default TTL is 24h; restart the service or
    implement token refresh when the token expires.

    Usage:
        async with BackendClient(
            base_url=settings.backend_api_url,
            jwt_token=settings.voice_agent_jwt,
        ) as client:
            call_resp = await client.create_call(
                property_id=uuid.UUID("..."),
                twilio_call_sid="CAabc123",
            )
            call_id = call_resp.id
    """

    def __init__(
        self,
        base_url: str,
        jwt_token: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """
        Args:
            base_url:    Root URL of the backend API, e.g. "http://localhost:8000".
            jwt_token:   HS256-signed JWT bearing company_id claim. Sent as
                         "Authorization: Bearer <token>" on every request.
                         Provisioned via VOICE_AGENT_JWT env var (Subbu).
            http_client: Optional pre-built httpx.AsyncClient (for testing / DI).
                         If None, a fresh client is created on __aenter__.
        """
        self._base_url = base_url.rstrip("/")
        self._jwt_token = jwt_token
        self._external_client = http_client
        self._http: httpx.AsyncClient | None = http_client
        log.info(
            "BackendClient created",
            extra={"base_url": self._base_url},
        )

    async def __aenter__(self) -> "BackendClient":
        if self._external_client is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                headers={"Authorization": f"Bearer {self._jwt_token}"},
                timeout=10.0,  # default; each method overrides per call
            )
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def close(self) -> None:
        """Close the underlying httpx client if we own it."""
        if self._http is not None and self._external_client is None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            raise RuntimeError(
                "BackendClient must be used as an async context manager "
                "or have close() called explicitly."
            )
        return self._http

    def _auth_headers(self) -> dict[str, str]:
        """Bearer JWT repeated per-request — never relies on client defaults alone."""
        return {"Authorization": f"Bearer {self._jwt_token}"}

    async def _get(self, path: str, timeout: float) -> dict[str, Any]:
        client = self._client()
        try:
            response = await client.get(
                path,
                headers=self._auth_headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise BackendToolError(
                code="TIMEOUT",
                message=f"GET {path} timed out after {timeout}s",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise BackendToolError(
                code="CONNECTION_ERROR",
                message=f"GET {path} connection failed: {exc}",
                retryable=True,
            ) from exc
        if response.is_success:
            return response.json()
        raise _parse_error(response)

    async def _post(
        self, path: str, payload: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        client = self._client()
        try:
            response = await client.post(
                path,
                json=payload,
                headers=self._auth_headers(),
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise BackendToolError(
                code="TIMEOUT",
                message=f"POST {path} timed out after {timeout}s",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise BackendToolError(
                code="CONNECTION_ERROR",
                message=f"POST {path} connection failed: {exc}",
                retryable=True,
            ) from exc
        if response.is_success:
            return response.json()
        raise _parse_error(response)

    # ------------------------------------------------------------------
    # Session bootstrap: create_call
    # Method: POST /v1/calls/
    # Schema: services/api/app/schemas/calls.py::CallCreateRequest
    # ------------------------------------------------------------------

    async def create_call(
        self,
        property_id: uuid.UUID,
        twilio_call_sid: str,
        livekit_room_id: str | None = None,
        caller_phone: str | None = None,
        started_at: str | None = None,
    ) -> CallCreateResponse:
        """
        Bootstrap a call record at the start of every inbound call.

        Path: POST /v1/calls/
        Timeout: 5s (blocking — no call_id without this)
        Status: 201 Created

        Must be the FIRST backend call in VoiceSession.start(). The returned
        id (call_id) is stored on CallState and used by all subsequent tool calls.

        Args:
            property_id:      Which property this call is for.
            twilio_call_sid:  Globally unique Twilio SID (CA...). Duplicate
                              inserts will fail with a DB constraint — do not retry.
            livekit_room_id:  LiveKit room identifier (optional in Phase 1).
            caller_phone:     Caller number from Twilio caller ID (PII — not logged).
            started_at:       ISO 8601 timestamp of call start. Defaults to None
                              (backend uses server time if absent).

        Returns:
            CallCreateResponse with .id = the call_id UUID.

        Raises:
            BackendToolError: retryable=False if PROPERTY_NOT_FOUND (config error).
                              retryable=True on connection/timeout errors.
        """
        payload: dict[str, Any] = {
            "property_id": str(property_id),
            "twilio_call_sid": twilio_call_sid,
        }
        if livekit_room_id is not None:
            payload["livekit_room_id"] = livekit_room_id
        if caller_phone is not None:
            payload["caller_phone"] = caller_phone  # PII — not logged below
        if started_at is not None:
            payload["started_at"] = started_at

        log.info(
            "create_call",
            extra={
                "property_id": str(property_id),
                "twilio_call_sid": twilio_call_sid,
                # caller_phone deliberately omitted (PII)
            },
        )
        data = await self._post(_PATH_POST_CALL, payload, timeout=_TIMEOUT_CREATE_CALL)
        return CallCreateResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 1: get_property_profile
    # Method: GET /v1/properties/{property_id}
    # ------------------------------------------------------------------

    async def get_property_profile(
        self,
        property_id: uuid.UUID,
    ) -> PropertyProfileResponse:
        """
        Load property context at call start.

        Path: GET /v1/properties/{property_id}
        Timeout: 500ms (read; likely cached)
        """
        path = _path_get_property(property_id)
        log.debug("get_property_profile", extra={"property_id": str(property_id)})
        data = await self._get(path, timeout=_TIMEOUT_PROPERTY_PROFILE)
        return PropertyProfileResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 2: search_property_knowledge
    # Method: POST /v1/voice/search-knowledge
    # ------------------------------------------------------------------

    async def search_property_knowledge(
        self,
        property_id: uuid.UUID,
        call_id: uuid.UUID,
        query: str,
        top_k: int = 5,
    ) -> SearchKnowledgeResponse:
        """
        RAG retrieval — Phase 3+.

        Path: POST /v1/voice/search-knowledge
        Timeout: 2s (vector search)

        Voice agent behavior on empty results.results:
          Say "I do not have that information" — never guess.
        Filter results where similarity_score < 0.5 before presenting to caller.
        """
        payload = {
            "property_id": str(property_id),
            "query": query,
            "call_id": str(call_id),
            "top_k": top_k,
        }
        log.debug(
            "search_property_knowledge",
            extra={"property_id": str(property_id), "top_k": top_k},
            # query omitted — may contain caller's words (PII-adjacent)
        )
        data = await self._post(
            _PATH_SEARCH_KNOWLEDGE, payload, timeout=_TIMEOUT_SEARCH_KNOWLEDGE
        )
        return SearchKnowledgeResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 3: create_or_update_lead
    # Method: POST /v1/voice/leads
    # ------------------------------------------------------------------

    async def create_or_update_lead(
        self,
        property_id: uuid.UUID,
        call_id: uuid.UUID,
        lead_fields: LeadFieldsInput,
    ) -> CreateOrUpdateLeadResponse:
        """
        Upsert a lead record.

        Path: POST /v1/voice/leads
        Timeout: 5s (database write)
        Idempotency: upserts on (property_id, phone). Safe to call incrementally
          as fields are captured during the conversation.

        Safety: Never populate lead_fields with invented values.
          Only pass what the caller explicitly stated.

        PII note: lead_fields contains name/phone/email — do not log them.
        """
        payload = {
            "property_id": str(property_id),
            "call_id": str(call_id),
            "lead_fields": lead_fields.model_dump(
                mode="json", exclude_none=True
            ),
        }
        log.debug(
            "create_or_update_lead",
            extra={"property_id": str(property_id), "call_id": str(call_id)},
            # lead_fields omitted — contains PII
        )
        data = await self._post(
            _PATH_POST_LEAD, payload, timeout=_TIMEOUT_LEAD
        )
        return CreateOrUpdateLeadResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 4: create_call_event
    # Method: POST /v1/voice/events
    # ------------------------------------------------------------------

    async def create_call_event(
        self,
        call_id: uuid.UUID,
        event_type: CallEventType,
        payload: dict[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> CreateCallEventResponse:
        """
        Emit a call lifecycle event.

        Path: POST /v1/voice/events
        Timeout: 5s
        Fire-and-forget in most cases — log failures but do not block the call.

        NOTE: 'occurred_at' is required by Harsha's schema (ISO 8601 string).
          If not supplied, the current UTC time is used.

        NOTE: 'property_id' is NOT in Harsha's CreateCallEventRequest body.
          Akhil's draft included it — it is absent here per the real schema.
        """
        import datetime

        body: dict[str, Any] = {
            "call_id": str(call_id),
            "event_type": event_type.value,
            "payload": payload or {},
            "occurred_at": occurred_at or datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        log.debug(
            "create_call_event",
            extra={"call_id": str(call_id), "event_type": event_type.value},
        )
        data = await self._post(
            _PATH_POST_EVENT, body, timeout=_TIMEOUT_CALL_EVENT
        )
        return CreateCallEventResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 5: save_transcript_segment
    # Method: POST /v1/voice/transcript-segment
    # ------------------------------------------------------------------

    async def save_transcript_segment(
        self,
        call_id: uuid.UUID,
        speaker: Literal["agent", "caller"],
        text: str,
        timestamp: float,
    ) -> SaveTranscriptSegmentResponse:
        """
        Persist one STT segment.

        Path: POST /v1/voice/transcript-segment
        Timeout: 5s (high-frequency; called after every utterance)

        IMPORTANT: 'timestamp' is float seconds-since-call-start (from
          Whisper/LiveKit), NOT an ISO datetime string.
          Use segment.timestamp_offset_seconds, not segment.created_at.

        NOTE: Client-supplied segment_id is NOT in Harsha's schema. The backend
          always generates a new UUID. Do not pass segment_id here.

        PII note: 'text' contains caller speech. Logged only at DEBUG with text omitted.
        """
        body: dict[str, Any] = {
            "call_id": str(call_id),
            "speaker": speaker,
            "text": text,
            "timestamp": timestamp,
        }
        log.debug(
            "save_transcript_segment",
            extra={"call_id": str(call_id), "speaker": speaker},
            # text deliberately omitted — PII
        )
        data = await self._post(
            _PATH_POST_TRANSCRIPT, body, timeout=_TIMEOUT_TRANSCRIPT
        )
        return SaveTranscriptSegmentResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 6: save_call_summary
    # Method: POST /v1/voice/call-summary
    # ------------------------------------------------------------------

    async def save_call_summary(
        self,
        call_id: uuid.UUID,
        summary: str,
        primary_intent: str,
        sentiment: Literal["positive", "neutral", "negative", "frustrated"] = "neutral",
        action_items: list[str] | None = None,
        escalation_flag: bool = False,
        lead_fields_extracted: dict[str, Any] | None = None,
        next_steps: str | None = None,
    ) -> SaveCallSummaryResponse:
        """
        Persist end-of-call summary.

        Path: POST /v1/voice/call-summary
        Timeout: 5s (called once at call end)

        IMPORTANT schema change from Akhil draft:
          - Harsha's schema does NOT include: property_id, booking_confirmed,
            booking_id, follow_up_email_sent, duration_seconds,
            confidence_score_final, tool_failure_count, escalation_reason.
          - Harsha's schema ADDS: summary (text), sentiment, action_items,
            lead_fields_extracted (dict snapshot), next_steps.
          - 'ai_summary' from draft -> 'summary' in real schema.
          Use CallState.summary_payload() to build the correct argument dict.
        """
        body: dict[str, Any] = {
            "call_id": str(call_id),
            "summary": summary,
            "primary_intent": primary_intent,
            "sentiment": sentiment,
            "action_items": action_items or [],
            "escalation_flag": escalation_flag,
            "lead_fields_extracted": lead_fields_extracted or {},
        }
        if next_steps is not None:
            body["next_steps"] = next_steps
        log.debug(
            "save_call_summary",
            extra={"call_id": str(call_id), "primary_intent": primary_intent},
        )
        data = await self._post(
            _PATH_POST_CALL_SUMMARY, body, timeout=_TIMEOUT_CALL_SUMMARY
        )
        return SaveCallSummaryResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 7: check_tour_availability
    # Method: POST /v1/voice/check-availability
    # ------------------------------------------------------------------

    async def check_tour_availability(
        self,
        property_id: uuid.UUID,
        start_date: date,
        end_date: date,
    ) -> CheckTourAvailabilityResponse:
        """
        Fetch open tour slots.

        Path: POST /v1/voice/check-availability
        Timeout: 3s (calendar read; per contract)

        IMPORTANT changes from Akhil draft:
          - 'call_id' is NOT in Harsha's CheckTourAvailabilityRequest.
          - 'duration_minutes' is NOT in the request (calendar adapter decides).
          - date_range uses date objects (not strings) in Python; serialised as
            "YYYY-MM-DD" in JSON by Pydantic.
          - Response slot shape: start_time/end_time as time objects.

        Voice agent behavior: present at most 3 slots to the caller.
          If available_slots is empty, do not invent slots — offer human follow-up.
        """
        payload = {
            "property_id": str(property_id),
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
        }
        log.debug(
            "check_tour_availability",
            extra={"property_id": str(property_id)},
        )
        data = await self._post(
            _PATH_CHECK_AVAILABILITY, payload, timeout=_TIMEOUT_CHECK_AVAILABILITY
        )
        return CheckTourAvailabilityResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 8: book_tour
    # Method: POST /v1/voice/book-tour
    # ------------------------------------------------------------------

    async def book_tour(
        self,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
        call_id: uuid.UUID,
        selected_slot: BookingSlotInput,
        tour_type: Literal["in_person", "self_guided", "virtual"] = "in_person",
    ) -> BookTourResponse:
        """
        Book a confirmed tour slot.

        Path: POST /v1/voice/book-tour
        Timeout: 5s (calendar write)

        SAFETY: Before calling this, the voice agent MUST have confirmed:
          - date and time with the caller
          - caller's name and contact details (lead record must exist)
          - property being toured
        If this raises BackendToolError, tell the caller the booking did NOT succeed.
        Do not continue as if the booking was confirmed.

        IMPORTANT changes from Akhil draft:
          - Path changed: /v1/voice/tours -> /v1/voice/book-tour
          - selected_slot uses BookingSlotInput (date, start_time, end_time, slot_id)
          - 'tour_type' added (default 'in_person').
          - Response adds 'tour_date', 'start_time'; loses 'confirmation_message'.
        """
        payload = {
            "property_id": str(property_id),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "selected_slot": {
                "date": selected_slot.date.isoformat(),
                "start_time": selected_slot.start_time.isoformat(),
                "end_time": selected_slot.end_time.isoformat(),
                "slot_id": selected_slot.slot_id,
            },
            "tour_type": tour_type,
        }
        log.info(
            "book_tour",
            extra={
                "property_id": str(property_id),
                "call_id": str(call_id),
                "tour_type": tour_type,
                # lead_id omitted from log — correlatable to PII
            },
        )
        data = await self._post(
            _PATH_BOOK_TOUR, payload, timeout=_TIMEOUT_BOOK_TOUR
        )
        return BookTourResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 9: send_follow_up_email
    # Method: POST /v1/voice/send-email
    # ------------------------------------------------------------------

    async def send_follow_up_email(
        self,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
        call_id: uuid.UUID,
        template_type: EmailTemplateType,
        context: dict[str, Any] | None = None,
    ) -> SendFollowUpEmailResponse:
        """
        Queue a follow-up email.

        Path: POST /v1/voice/send-email
        Timeout: 5s (async delivery acceptable)

        SAFETY: email_confirmed must be True in CallState before calling this.
          The agent must have read back the address and the caller must have
          confirmed it verbally.

        IMPORTANT changes from Akhil draft:
          - Path changed: /v1/voice/emails -> /v1/voice/send-email
          - 'recipient_email' is NOT in Harsha's request schema. Backend looks
            up the email from lead_id. Confirm email at the lead record level.
          - Template types changed: general_followup -> follow_up or general;
            maintenance_acknowledgment is not in Harsha's enum.
          - Response now includes 'recipient', 'subject', 'delivery_status'.

        PII note: recipient email is not passed here — backend manages it from
          lead record. Log only template_type and call_id.
        """
        payload: dict[str, Any] = {
            "property_id": str(property_id),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "template_type": template_type.value,
            "context": context or {},
        }
        log.debug(
            "send_follow_up_email",
            extra={
                "call_id": str(call_id),
                "template_type": template_type.value,
                # lead_id omitted from log — correlatable to PII
            },
        )
        data = await self._post(
            _PATH_SEND_EMAIL, payload, timeout=_TIMEOUT_SEND_EMAIL
        )
        return SendFollowUpEmailResponse.model_validate(data)

    # ------------------------------------------------------------------
    # Tool 10: request_human_handoff
    # Method: POST /v1/voice/request-handoff
    # ------------------------------------------------------------------

    async def request_human_handoff(
        self,
        property_id: uuid.UUID,
        call_id: uuid.UUID,
        reason: str,
        urgency: HandoffUrgency,
        lead_id: uuid.UUID | None = None,
    ) -> RequestHandoffResponse:
        """
        Trigger human handoff.

        Path: POST /v1/voice/request-handoff
        Timeout: 5s (must not block caller disconnect sequence)

        Called for: Fair Housing questions, legal/financial advice requests,
          emergencies, low-confidence situations, tool failures, explicit
          caller request, booking failure after retries.

        IMPORTANT changes from Akhil draft:
          - Path changed: /v1/voice/calls/{call_id}/handoff -> /v1/voice/request-handoff
          - 'reason' is a free-text string (max 1000 chars), NOT an enum.
            EscalationReason enum values from CallState can be used as the reason
            text directly (pass .value).
          - 'urgency' is REQUIRED (HandoffUrgency enum: low/medium/high/emergency).
            Use EscalationReason.to_handoff_urgency() to derive it automatically.
          - 'notes', 'caller_phone_number', 'escalation_contact' are NOT in
            Harsha's schema. Omit them.
          - 'lead_id' is optional UUID.

        Voice agent behavior after this call succeeds:
          Tell the caller: "I'm connecting you with our team. Someone will follow
          up with you shortly." Then end the LiveKit session.
        """
        body: dict[str, Any] = {
            "property_id": str(property_id),
            "call_id": str(call_id),
            "reason": reason,
            "urgency": urgency.value,
        }
        if lead_id is not None:
            body["lead_id"] = str(lead_id)
        log.info(
            "request_human_handoff",
            extra={
                "call_id": str(call_id),
                "reason": reason,
                "urgency": urgency.value,
                # lead_id omitted from log
            },
        )
        data = await self._post(
            _PATH_REQUEST_HANDOFF, body, timeout=_TIMEOUT_HANDOFF
        )
        return RequestHandoffResponse.model_validate(data)
