"""
Integration tests for VoiceSession Phase 2 orchestration.

Tests verify that the per-turn loop (handle_caller_turn) correctly wires:
  - EscalationDetector (safety gate runs first)
  - LeadCaptureStateMachine (phase + field tracking)
  - ConfidenceEvaluator (via evaluate_llm_response)
  - SummaryBuilder (via build_summary)

All tests drive the session with synthetic caller text strings.
BackendClient is mocked — no real HTTP, no audio, no LLM providers.

Scenarios:
  - Fair Housing keyword triggers escalation, returns early
  - Emergency keyword triggers EMERGENCY escalation
  - Safe leasing inquiry progresses through lead capture
  - Tool failure accumulation at threshold triggers escalation
  - Low-confidence LLM response triggers LOW_CONFIDENCE escalation
  - Confident LLM response does not trigger escalation
  - build_summary() produces correct shape after call ends
  - Confirmation gate wired: handle_caller_turn with action_ready blocks
    into AWAITING_CONFIRMATION; resolve via resolve_confirmation
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from voice_agent.agent.session import VoiceSession
from voice_agent.conversation.state_machine import ConversationPhase
from voice_agent.state.call_state import CallPhase, EscalationReason
from voice_agent.tools.backend_client import BackendClient, CallCreateResponse

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_PROPERTY_ID = str(uuid.uuid4())
_COMPANY_ID = str(uuid.uuid4())
# Placeholder JWT used in tests — tests do not verify token signature.
_JWT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig"
_BACKEND_CALL_ID = str(uuid.uuid4())


@pytest.fixture
def mock_client() -> AsyncMock:
    """BackendClient mock — no real HTTP."""
    client = AsyncMock(spec=BackendClient)
    client.create_call.return_value = CallCreateResponse(
        id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        status="active",
    )
    client.create_call_event.return_value = None
    client.save_call_summary.return_value = None
    client.request_human_handoff.return_value = None
    return client


@pytest.fixture
def session(mock_client: AsyncMock) -> VoiceSession:
    """VoiceSession with mocked backend — ready for per-turn tests."""
    return VoiceSession(
        property_id=_PROPERTY_ID,
        jwt_token=_JWT_TOKEN,
        backend_client=mock_client,
        twilio_call_sid="CA_test",
    )


# ---------------------------------------------------------------------------
# Escalation detection via handle_caller_turn
# ---------------------------------------------------------------------------


class TestHandleCallerTurnEscalation:
    @pytest.mark.asyncio
    async def test_fair_housing_keyword_triggers_escalation(self, session: VoiceSession):
        result = await session.handle_caller_turn(
            "What race of people live there?"
        )
        assert result["escalated"] is True
        assert result["escalation_reason"] == EscalationReason.FAIR_HOUSING_QUESTION.value
        assert result["phase"] == ConversationPhase.ESCALATION.value

    @pytest.mark.asyncio
    async def test_emergency_keyword_triggers_emergency_escalation(self, session: VoiceSession):
        result = await session.handle_caller_turn(
            "There is a gas leak in my apartment!"
        )
        assert result["escalated"] is True
        assert result["escalation_reason"] == EscalationReason.EMERGENCY.value

    @pytest.mark.asyncio
    async def test_escalation_sets_call_state_flag(self, session: VoiceSession):
        await session.handle_caller_turn("There's a fire!")
        assert session.state.escalation_flag is True
        assert session.state.escalation_reason == EscalationReason.EMERGENCY

    @pytest.mark.asyncio
    async def test_escalation_syncs_call_phase(self, session: VoiceSession):
        await session.handle_caller_turn("Can I sue my landlord?")
        assert session.state.phase == CallPhase.ESCALATION

    @pytest.mark.asyncio
    async def test_tool_failure_threshold_triggers_escalation(self, session: VoiceSession):
        """3 tool failures on CallState triggers BACKEND_TOOL_FAILURE escalation."""
        session.state.tool_failure_count = 3
        result = await session.handle_caller_turn("I'd like to book a tour")
        assert result["escalated"] is True
        assert result["escalation_reason"] == EscalationReason.BACKEND_TOOL_FAILURE.value

    @pytest.mark.asyncio
    async def test_safe_text_no_escalation(self, session: VoiceSession):
        result = await session.handle_caller_turn("I'm interested in a one-bedroom apartment")
        assert result["escalated"] is False
        assert result["escalation_reason"] is None


# ---------------------------------------------------------------------------
# State machine progression via handle_caller_turn
# ---------------------------------------------------------------------------


class TestHandleCallerTurnStateMachine:
    @pytest.mark.asyncio
    async def test_leasing_inquiry_moves_to_lead_capture(self, session: VoiceSession):
        result = await session.handle_caller_turn("I want to rent an apartment")
        assert result["phase"] == ConversationPhase.LEAD_CAPTURE.value

    @pytest.mark.asyncio
    async def test_first_missing_field_is_name(self, session: VoiceSession):
        result = await session.handle_caller_turn("I'd like to learn about apartment availability")
        assert result["next_field_to_ask"] == "name"

    @pytest.mark.asyncio
    async def test_field_capture_reduces_missing_list(self, session: VoiceSession):
        await session.handle_caller_turn("I want to rent")
        result = await session.handle_caller_turn(
            "My name is Alex", extracted_fields={"name": "Alex"}
        )
        assert "name" not in result["missing_fields"]
        assert result["next_field_to_ask"] == "phone"

    @pytest.mark.asyncio
    async def test_all_fields_with_action_ready_enters_confirmation(self, session: VoiceSession):
        await session.handle_caller_turn("I want to rent an apartment")
        await session.handle_caller_turn("Alex", extracted_fields={"name": "Alex"})
        await session.handle_caller_turn("555", extracted_fields={"phone": "555"})
        await session.handle_caller_turn("a@b.com", extracted_fields={"email": "a@b.com"})
        await session.handle_caller_turn("2BR", extracted_fields={"desired_unit_type": "2BR"})
        result = await session.handle_caller_turn(
            "June 1st",
            extracted_fields={"move_in_date": "2026-06-01"},
            action_ready=True,
        )
        assert result["phase"] == ConversationPhase.AWAITING_CONFIRMATION.value
        assert result["confirmation_needed"] is True

    @pytest.mark.asyncio
    async def test_maintenance_question_moves_to_resident_support(self, session: VoiceSession):
        result = await session.handle_caller_turn(
            "I have a maintenance issue with my broken heater"
        )
        assert result["phase"] == ConversationPhase.RESIDENT_SUPPORT.value

    @pytest.mark.asyncio
    async def test_state_machine_accessible_on_session(self, session: VoiceSession):
        """VoiceSession exposes state_machine for external resolve_confirmation."""
        assert session.state_machine is not None
        assert isinstance(session.state_machine, __import__(
            "voice_agent.conversation.state_machine",
            fromlist=["LeadCaptureStateMachine"]
        ).LeadCaptureStateMachine)


# ---------------------------------------------------------------------------
# Low-confidence detection via evaluate_llm_response
# ---------------------------------------------------------------------------


class TestEvaluateLlmResponse:
    @pytest.mark.asyncio
    async def test_hedging_response_triggers_low_confidence_escalation(
        self, session: VoiceSession
    ):
        result = await session.evaluate_llm_response(
            "I'm not sure what the pet policy is. I don't know the exact rules."
        )
        assert result["is_low"] is True
        assert result["escalation_triggered"] is True
        assert session.state.escalation_flag is True
        assert session.state.escalation_reason == EscalationReason.LOW_CONFIDENCE

    @pytest.mark.asyncio
    async def test_confident_response_no_escalation(self, session: VoiceSession):
        result = await session.evaluate_llm_response(
            "The monthly rent for a one-bedroom is $1,450, "
            "utilities not included. Tours are available Monday through Friday."
        )
        assert result["is_low"] is False
        assert result["escalation_triggered"] is False
        assert session.state.escalation_flag is False

    @pytest.mark.asyncio
    async def test_second_low_confidence_does_not_double_escalate(
        self, session: VoiceSession
    ):
        """If already escalated, a second low-confidence response should not re-trigger."""
        # Use a triple-hedge to push well below threshold 0.4
        await session.evaluate_llm_response(
            "I'm not sure and I don't know. Maybe it depends."
        )
        assert session.state.escalation_flag is True

        # Second evaluation — escalation_flag already True
        result = await session.evaluate_llm_response(
            "I'm not sure and I don't know that either."
        )
        # escalation_triggered should be False because flag was already set
        assert result["escalation_triggered"] is False

    @pytest.mark.asyncio
    async def test_confidence_score_returned_in_result(self, session: VoiceSession):
        result = await session.evaluate_llm_response(
            "The rent is $1,400 per month."
        )
        assert "confidence" in result
        assert 0.0 <= result["confidence"] <= 1.0

    @pytest.mark.asyncio
    async def test_low_confidence_reason_in_result_when_low(self, session: VoiceSession):
        result = await session.evaluate_llm_response("I'm not sure and I don't know.")
        assert result["low_confidence_reason"] is not None

    @pytest.mark.asyncio
    async def test_low_confidence_reason_none_when_high(self, session: VoiceSession):
        result = await session.evaluate_llm_response(
            "The property has a rooftop pool and a gym, both open 24/7."
        )
        assert result["low_confidence_reason"] is None


# ---------------------------------------------------------------------------
# build_summary integration
# ---------------------------------------------------------------------------


class TestBuildSummaryIntegration:
    def test_build_summary_returns_correct_schema_shape(self, session: VoiceSession):
        session.state.mark_ended()
        payload = session.build_summary()

        required_keys = {
            "call_id",
            "summary",
            "primary_intent",
            "sentiment",
            "action_items",
            "escalation_flag",
            "lead_fields_extracted",
            "next_steps",
        }
        for key in required_keys:
            assert key in payload, f"Missing key: {key}"

    def test_build_summary_call_id_matches_state(self, session: VoiceSession):
        session.state.mark_ended()
        payload = session.build_summary()
        assert payload["call_id"] == session.state.call_id

    @pytest.mark.asyncio
    async def test_build_summary_uses_state_machine_intent(self, session: VoiceSession):
        # Drive a leasing turn so state machine records intent
        await session.handle_caller_turn("I want to rent an apartment")
        session.state.mark_ended()
        payload = session.build_summary()
        assert payload["primary_intent"] == "leasing_inquiry"

    @pytest.mark.asyncio
    async def test_build_summary_lead_fields_match_captured(self, session: VoiceSession):
        await session.handle_caller_turn("I want to rent")
        await session.handle_caller_turn(
            "Jordan", extracted_fields={"name": "Jordan Lee"}
        )
        # Copy captured fields to CallState.lead_fields (simulating VoiceSession wiring)
        captured = session.state_machine.captured_fields.as_dict()
        session.state.lead_fields.name = captured.get("name")
        session.state.mark_ended()

        payload = session.build_summary()
        assert payload["lead_fields_extracted"].get("name") == "Jordan Lee"

    @pytest.mark.asyncio
    async def test_build_summary_escalation_flag_reflects_state(self, session: VoiceSession):
        await session.handle_caller_turn("There's a fire!")  # triggers escalation
        session.state.mark_ended()
        payload = session.build_summary()
        assert payload["escalation_flag"] is True
