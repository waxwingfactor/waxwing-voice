"""
Unit tests for EscalationReason.to_handoff_urgency() mapping.

Covers every branch of the mapping table from docs/contracts/voice-tools.akhil-draft.md OQ-13.
Also tests BackendClient.create_call() request shape via mocked HTTP (deliverable 3).

No backend connections needed — all HTTP is mocked with httpx.MockTransport.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from voice_agent.providers.tts.mock import MockTTSAdapter
from voice_agent.state.call_state import EscalationReason, HandoffUrgency
from voice_agent.tools.backend_client import (
    BackendClient,
    CallCreateResponse,
)


# ---------------------------------------------------------------------------
# EscalationReason -> HandoffUrgency mapping
# ---------------------------------------------------------------------------


class TestEscalationReasonToHandoffUrgency:
    """Every mapping branch must hit the expected HandoffUrgency value."""

    def test_emergency_maps_to_emergency(self):
        assert EscalationReason.EMERGENCY.to_handoff_urgency() == HandoffUrgency.EMERGENCY

    def test_fair_housing_maps_to_high(self):
        assert EscalationReason.FAIR_HOUSING_QUESTION.to_handoff_urgency() == HandoffUrgency.HIGH

    def test_legal_question_maps_to_high(self):
        assert EscalationReason.LEGAL_QUESTION.to_handoff_urgency() == HandoffUrgency.HIGH

    def test_financial_advice_maps_to_high(self):
        assert EscalationReason.FINANCIAL_ADVICE_REQUESTED.to_handoff_urgency() == HandoffUrgency.HIGH

    def test_eligibility_question_maps_to_high(self):
        assert EscalationReason.ELIGIBILITY_QUESTION.to_handoff_urgency() == HandoffUrgency.HIGH

    def test_caller_requested_maps_to_high(self):
        """Explicit caller request for a human is treated as high urgency."""
        assert EscalationReason.CALLER_REQUESTED.to_handoff_urgency() == HandoffUrgency.HIGH

    def test_low_confidence_maps_to_medium(self):
        assert EscalationReason.LOW_CONFIDENCE.to_handoff_urgency() == HandoffUrgency.MEDIUM

    def test_backend_tool_failure_maps_to_medium(self):
        assert EscalationReason.BACKEND_TOOL_FAILURE.to_handoff_urgency() == HandoffUrgency.MEDIUM

    def test_booking_failed_maps_to_medium(self):
        assert EscalationReason.BOOKING_FAILED.to_handoff_urgency() == HandoffUrgency.MEDIUM

    def test_unknown_maps_to_medium(self):
        """Unknown reasons default to medium — conservative escalation."""
        assert EscalationReason.UNKNOWN.to_handoff_urgency() == HandoffUrgency.MEDIUM

    def test_all_reasons_covered(self):
        """Every EscalationReason value must return a HandoffUrgency (no KeyError)."""
        for reason in EscalationReason:
            urgency = reason.to_handoff_urgency()
            assert isinstance(urgency, HandoffUrgency), (
                f"EscalationReason.{reason.name}.to_handoff_urgency() returned "
                f"{urgency!r}, expected a HandoffUrgency instance"
            )

    def test_urgency_values_are_valid_backend_strings(self):
        """
        to_handoff_urgency() values must match what Harsha's backend accepts.
        Backend enum (voice_tools.py::HandoffUrgency): low, medium, high, emergency.
        """
        valid_backend_values = {"low", "medium", "high", "emergency"}
        for reason in EscalationReason:
            urgency = reason.to_handoff_urgency()
            assert urgency.value in valid_backend_values, (
                f"EscalationReason.{reason.name} -> {urgency.value!r} "
                f"is not a valid HandoffUrgency backend value"
            )

    def test_high_urgency_reasons_are_complete(self):
        """
        All reasons that should map to HIGH are accounted for.
        This test catches the case where a new EscalationReason is added to the
        enum without being explicitly mapped in _ESCALATION_URGENCY_MAP.
        """
        expected_high = {
            EscalationReason.FAIR_HOUSING_QUESTION,
            EscalationReason.LEGAL_QUESTION,
            EscalationReason.FINANCIAL_ADVICE_REQUESTED,
            EscalationReason.ELIGIBILITY_QUESTION,
            EscalationReason.CALLER_REQUESTED,
        }
        for reason in expected_high:
            assert reason.to_handoff_urgency() == HandoffUrgency.HIGH, (
                f"EscalationReason.{reason.name} should map to HIGH urgency"
            )

    def test_emergency_is_unique_emergency_level(self):
        """Only EMERGENCY should map to the emergency urgency level."""
        emergency_reasons = [
            r for r in EscalationReason
            if r.to_handoff_urgency() == HandoffUrgency.EMERGENCY
        ]
        assert emergency_reasons == [EscalationReason.EMERGENCY], (
            f"Expected only EMERGENCY to map to emergency urgency, "
            f"got: {[r.name for r in emergency_reasons]}"
        )


# ---------------------------------------------------------------------------
# BackendClient.create_call() — mocked HTTP request shape test
# ---------------------------------------------------------------------------


class FakeTransport(httpx.AsyncBaseTransport):
    """
    Minimal async transport that captures the last request and returns
    a configurable response body.
    """

    def __init__(self, response_body: dict, status_code: int = 201) -> None:
        self._body = json.dumps(response_body).encode()
        self._status_code = status_code
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=self._body,
        )


class FakeTransportWithHeaders(httpx.AsyncBaseTransport):
    """
    Like FakeTransport but supports injecting custom response headers.
    Used for 429 rate-limit tests to inject Retry-After.
    """

    def __init__(
        self,
        response_body: dict,
        status_code: int,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._body = json.dumps(response_body).encode()
        self._status_code = status_code
        self._extra_headers = extra_headers or {}
        self.last_request: httpx.Request | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.last_request = request
        headers = {"content-type": "application/json", **self._extra_headers}
        return httpx.Response(
            status_code=self._status_code,
            headers=headers,
            content=self._body,
        )


_FAKE_CALL_ID = str(uuid.uuid4())
_FAKE_PROPERTY_ID = str(uuid.uuid4())
_FAKE_COMPANY_ID = str(uuid.uuid4())
# Placeholder JWT string used in tests — tests do not verify token signature.
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoiZmFrZSJ9.fake_sig"


@pytest.fixture
def call_create_response() -> dict:
    return {
        "id": _FAKE_CALL_ID,
        "property_id": _FAKE_PROPERTY_ID,
        "status": "active",
        "created_at": "2026-05-09T12:00:00Z",
    }


class TestBackendClientCreateCall:
    """Verify that create_call() sends the correct body and headers."""

    @pytest.mark.asyncio
    async def test_create_call_returns_call_id(self, call_create_response):
        transport = FakeTransport(call_create_response, status_code=201)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        result = await client.create_call(
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            twilio_call_sid="CA_test_sid",
        )

        assert isinstance(result, CallCreateResponse)
        assert str(result.id) == _FAKE_CALL_ID
        assert str(result.property_id) == _FAKE_PROPERTY_ID
        assert result.status == "active"

    @pytest.mark.asyncio
    async def test_create_call_posts_to_correct_path(self, call_create_response):
        transport = FakeTransport(call_create_response, status_code=201)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        await client.create_call(
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            twilio_call_sid="CA_test_sid",
        )

        req = transport.last_request
        assert req is not None
        assert req.method == "POST"
        assert req.url.path == "/v1/calls/"

    @pytest.mark.asyncio
    async def test_create_call_sends_bearer_jwt_header(self, call_create_response):
        transport = FakeTransport(call_create_response, status_code=201)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        await client.create_call(
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            twilio_call_sid="CA_test_sid",
        )

        req = transport.last_request
        assert req.headers.get("authorization") == f"Bearer {_FAKE_JWT}"

    @pytest.mark.asyncio
    async def test_create_call_body_shape(self, call_create_response):
        """
        POST body must match CallCreateRequest from services/api/app/schemas/calls.py:
          property_id, twilio_call_sid — required
          livekit_room_id, caller_phone, started_at — optional
        """
        transport = FakeTransport(call_create_response, status_code=201)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        await client.create_call(
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            twilio_call_sid="CA_test_sid",
            livekit_room_id="room_abc",
            started_at="2026-05-09T12:00:00Z",
            # caller_phone deliberately omitted (PII)
        )

        req = transport.last_request
        body = json.loads(req.content)

        assert body["property_id"] == _FAKE_PROPERTY_ID
        assert body["twilio_call_sid"] == "CA_test_sid"
        assert body["livekit_room_id"] == "room_abc"
        assert body["started_at"] == "2026-05-09T12:00:00Z"
        # caller_phone absent because it was not passed
        assert "caller_phone" not in body

    @pytest.mark.asyncio
    async def test_create_call_omits_none_optional_fields(self, call_create_response):
        """Optional None args must not appear in the request body."""
        transport = FakeTransport(call_create_response, status_code=201)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        await client.create_call(
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            twilio_call_sid="CA_minimal",
        )

        req = transport.last_request
        body = json.loads(req.content)
        assert "livekit_room_id" not in body
        assert "caller_phone" not in body
        assert "started_at" not in body

    @pytest.mark.asyncio
    async def test_create_call_raises_on_4xx(self):
        """Non-2xx responses must raise BackendToolError."""
        from voice_agent.tools.backend_client import BackendToolError

        error_body = {
            "error": {
                "code": "PROPERTY_NOT_FOUND",
                "message": "Property not found.",
                "retryable": False,
            }
        }
        transport = FakeTransport(error_body, status_code=404)
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        with pytest.raises(BackendToolError) as exc_info:
            await client.create_call(
                property_id=uuid.UUID(_FAKE_PROPERTY_ID),
                twilio_call_sid="CA_bad",
            )

        err = exc_info.value
        assert err.code == "PROPERTY_NOT_FOUND"
        assert err.retryable is False
        assert err.status_code == 404

    @pytest.mark.asyncio
    async def test_rate_limit_429_with_retry_after(self):
        """
        429 with Retry-After: 2 header -> BackendToolError raised,
        retryable=True, error_code='RATE_LIMITED', retry_after_seconds=2.
        """
        from voice_agent.tools.backend_client import BackendToolError

        transport = FakeTransportWithHeaders(
            response_body={},
            status_code=429,
            extra_headers={"retry-after": "2"},
        )
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        with pytest.raises(BackendToolError) as exc_info:
            await client.create_call(
                property_id=uuid.UUID(_FAKE_PROPERTY_ID),
                twilio_call_sid="CA_rate_limited",
            )

        err = exc_info.value
        assert err.code == "RATE_LIMITED"
        assert err.retryable is True
        assert err.status_code == 429
        assert err.retry_after_seconds == 2

    @pytest.mark.asyncio
    async def test_rate_limit_429_without_retry_after(self):
        """
        429 with no Retry-After header -> BackendToolError raised,
        retryable=True, error_code='RATE_LIMITED', retry_after_seconds=None.
        """
        from voice_agent.tools.backend_client import BackendToolError

        transport = FakeTransportWithHeaders(
            response_body={},
            status_code=429,
            # No Retry-After header
        )
        http_client = httpx.AsyncClient(
            base_url="http://test",
            transport=transport,
        )
        client = BackendClient(
            base_url="http://test",
            jwt_token=_FAKE_JWT,
            http_client=http_client,
        )

        with pytest.raises(BackendToolError) as exc_info:
            await client.create_call(
                property_id=uuid.UUID(_FAKE_PROPERTY_ID),
                twilio_call_sid="CA_rate_limited_no_header",
            )

        err = exc_info.value
        assert err.code == "RATE_LIMITED"
        assert err.retryable is True
        assert err.status_code == 429
        assert err.retry_after_seconds is None


# ---------------------------------------------------------------------------
# VoiceSession.start() integration — backend_call_id stored on CallState
# ---------------------------------------------------------------------------


class TestVoiceSessionStart:
    """
    Verify VoiceSession.start() calls create_call and stores backend_call_id.
    BackendClient is injected as a mock — no real HTTP.
    """

    @pytest.mark.asyncio
    async def test_start_sets_backend_call_id(self):
        from voice_agent.agent.session import VoiceSession

        returned_id = uuid.uuid4()
        mock_client = AsyncMock(spec=BackendClient)
        mock_client.create_call.return_value = CallCreateResponse(
            id=returned_id,
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            status="active",
        )
        # create_call_event is called as fire-and-forget after create_call
        mock_client.create_call_event.return_value = AsyncMock()

        session = VoiceSession(
            property_id=_FAKE_PROPERTY_ID,
            jwt_token=_FAKE_JWT,
            backend_client=mock_client,
            tts_adapter=MockTTSAdapter(),
            twilio_call_sid="CA_test",
        )

        await session.start()

        assert session.state.backend_call_id == str(returned_id)

    @pytest.mark.asyncio
    async def test_start_calls_create_call_with_correct_args(self):
        from voice_agent.agent.session import VoiceSession

        returned_id = uuid.uuid4()
        mock_client = AsyncMock(spec=BackendClient)
        mock_client.create_call.return_value = CallCreateResponse(
            id=returned_id,
            property_id=uuid.UUID(_FAKE_PROPERTY_ID),
            status="active",
        )
        mock_client.create_call_event.return_value = AsyncMock()

        session = VoiceSession(
            property_id=_FAKE_PROPERTY_ID,
            jwt_token=_FAKE_JWT,
            backend_client=mock_client,
            tts_adapter=MockTTSAdapter(),
            twilio_call_sid="CA_session_test",
            livekit_room_id="room_xyz",
        )

        await session.start()

        mock_client.create_call.assert_called_once()
        call_kwargs = mock_client.create_call.call_args.kwargs
        assert str(call_kwargs["property_id"]) == _FAKE_PROPERTY_ID
        assert call_kwargs["twilio_call_sid"] == "CA_session_test"
        assert call_kwargs["livekit_room_id"] == "room_xyz"

    @pytest.mark.asyncio
    async def test_start_propagates_create_call_failure(self):
        """If create_call raises, start() must re-raise — no backend_call_id set."""
        from voice_agent.agent.session import VoiceSession
        from voice_agent.tools.backend_client import BackendToolError

        mock_client = AsyncMock(spec=BackendClient)
        mock_client.create_call.side_effect = BackendToolError(
            code="PROPERTY_NOT_FOUND",
            message="Property not found.",
            retryable=False,
            status_code=404,
        )

        session = VoiceSession(
            property_id=_FAKE_PROPERTY_ID,
            jwt_token=_FAKE_JWT,
            backend_client=mock_client,
            tts_adapter=MockTTSAdapter(),
            twilio_call_sid="CA_fail",
        )

        with pytest.raises(BackendToolError) as exc_info:
            await session.start()

        assert exc_info.value.code == "PROPERTY_NOT_FOUND"
        # backend_call_id must remain None — do not proceed
        assert session.state.backend_call_id is None
