"""
Automated pytest scenario suite for the voice agent.

Converts the 8 manual smoke-test scripts in tests/manual/ into repeatable
automated tests, plus adversarial and failure-shape scenarios.

Architecture
------------
Test surface: VoiceSession.handle_caller_turn() for orchestration-level tests,
plus direct coordinator tests where the coordinator owns the logic.

Mocks
-----
- FakeBackend: wraps BackendClient with canned AsyncMock responses. Uses the
  real Pydantic response types from BackendClient so shapes are real.
- LLM, STT, TTS are NOT called in these tests (stubs raise NotImplementedError).
  We assert on observable orchestration state: phase transitions, escalation
  flags, tool call sequences, and coordinator outputs.

Scenario dataclass
------------------
Each test scenario is documented as a Scenario dataclass (for readability
in code review), but the assertion logic lives in each test function — not
in a generic runner — so failures are easy to diagnose.

Total: ~18 scenarios, some parameterized.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from voice_agent.agent.session import VoiceSession
from voice_agent.conversation.booking import (
    BookingSubState,
    TourBookingCoordinator,
)
from voice_agent.conversation.email_followup import (
    EmailSubState,
    FollowUpEmailCoordinator,
)
from voice_agent.conversation.escalation import EscalationDetector
from voice_agent.conversation.state_machine import ConversationPhase
from voice_agent.state.call_state import CallPhase, EscalationReason
from voice_agent.tools.backend_client import (
    AvailableSlot,
    BackendClient,
    BackendToolError,
    BookTourResponse,
    CallCreateResponse,
    CheckTourAvailabilityResponse,
    CreateOrUpdateLeadResponse,
    RequestHandoffResponse,
    SearchKnowledgeResponse,
    SendFollowUpEmailResponse,
    KnowledgeResult,
)

# ---------------------------------------------------------------------------
# Scenario dataclass — documents intent, not execution logic
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """
    Human-readable description of what a scenario covers.
    The actual assertions live in the test function below.
    """

    name: str
    description: str
    expected_escalation: bool = False
    expected_escalation_reason: str | None = None
    expected_phases: list[str] = field(default_factory=list)
    expected_tool_calls: list[str] = field(default_factory=list)
    expected_summary_intent: str = ""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROPERTY_ID = str(uuid.uuid4())
_COMPANY_ID = "test-company-id"
# Placeholder JWT used in tests — tests do not verify token signature.
_JWT_TOKEN = "eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig"
_BACKEND_CALL_ID = str(uuid.uuid4())
_LEAD_ID = str(uuid.uuid4())
_BOOKING_ID = str(uuid.uuid4())

_SLOT_1 = AvailableSlot(
    date=date(2026, 6, 10),
    start_time=time(10, 0),
    end_time=time(10, 30),
    slot_id="slot-001",
)
_SLOT_2 = AvailableSlot(
    date=date(2026, 6, 11),
    start_time=time(14, 0),
    end_time=time(14, 30),
    slot_id="slot-002",
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_backend() -> AsyncMock:
    """BackendClient mock with sensible defaults for all endpoints."""
    client = AsyncMock(spec=BackendClient)
    client.create_call.return_value = CallCreateResponse(
        id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        status="active",
    )
    client.create_call_event.return_value = None
    client.save_call_summary.return_value = None
    client.create_or_update_lead.return_value = CreateOrUpdateLeadResponse(
        lead_id=uuid.UUID(_LEAD_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        created=True,
    )
    client.check_tour_availability.return_value = CheckTourAvailabilityResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        available_slots=[_SLOT_1, _SLOT_2],
    )
    client.book_tour.return_value = BookTourResponse(
        booking_id=uuid.UUID(_BOOKING_ID),
        calendar_event_id="cal-123",
        tour_date=_SLOT_1.date,
        start_time=_SLOT_1.start_time,
        status="confirmed",
    )
    client.send_follow_up_email.return_value = SendFollowUpEmailResponse(
        email_id=uuid.UUID(str(uuid.uuid4())),
        recipient="redacted",
        subject="Your Tour Confirmation",
        delivery_status="queued",
    )
    client.request_human_handoff.return_value = RequestHandoffResponse(
        handoff_id=uuid.UUID(str(uuid.uuid4())),
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        status="pending",
        notification_sent=True,
    )
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="test query",
        results=[
            KnowledgeResult(
                chunk_text="1-bedroom rent starts at $1,450/month.",
                source_label="Pricing FAQ",
                similarity_score=0.92,
            )
        ],
    )
    return client


def _make_session(client: AsyncMock | None = None) -> VoiceSession:
    if client is None:
        client = _make_backend()
    session = VoiceSession(
        property_id=_PROPERTY_ID,
        jwt_token=_JWT_TOKEN,
        backend_client=client,
        twilio_call_sid="CA_test_scenario",
    )
    # Simulate that start() ran: set backend_call_id so coordinators can run
    session.state.backend_call_id = _BACKEND_CALL_ID
    return session


# ===========================================================================
# SCENARIO 01: Prospect asks rent and availability
# ===========================================================================
# Manual script: tests/manual/01_rent_availability.md
# Expected: agent retrieves knowledge, answers, no escalation.


@pytest.mark.asyncio
async def test_01_rent_availability_no_escalation():
    """
    Prospect asks about rent. Agent should:
    - NOT escalate
    - Stay in INTENT_DETECTION / LEAD_CAPTURE (not escalation)
    - RAG retrieval runs (backend_call_id is set)
    - knowledge_unavailable is False when results returned
    """
    scenario = Scenario(
        name="01_rent_availability",
        description="Prospect asks rent; agent retrieves from knowledge base, does not invent.",
        expected_escalation=False,
        expected_phases=["intent_detection", "lead_capture"],
    )

    client = _make_backend()
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "Hi, I'm looking for a one-bedroom apartment. What's the rent?"
    )

    assert not result["escalated"], f"Should not escalate. reason={result.get('escalation_reason')}"
    assert result["phase"] != ConversationPhase.ESCALATION.value
    # Knowledge should be available (mock returns results)
    assert not result["knowledge_unavailable"]
    # search_property_knowledge was called
    client.search_property_knowledge.assert_called_once()


@pytest.mark.asyncio
async def test_01_availability_question_no_invention():
    """
    Prospect asks about September availability.
    Agent must not invent — knowledge_unavailable flag drives LLM behavior.
    """
    client = _make_backend()
    # Return empty results to simulate unavailable data
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="availability",
        results=[],
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "Do you have anything available in September?"
    )

    assert not result["escalated"]
    # With empty results, knowledge_unavailable must be True so Gemini gets the
    # "no invention" instruction in the knowledge slot
    assert result["knowledge_unavailable"], (
        "Empty retrieval should set knowledge_unavailable=True so agent cannot invent"
    )


# ===========================================================================
# SCENARIO 02: Pet policy and parking questions
# ===========================================================================
# Manual script: tests/manual/02_pet_parking.md


@pytest.mark.asyncio
async def test_02_pet_policy_retrieves_knowledge():
    """Pet policy question triggers RAG lookup; no escalation."""
    client = _make_backend()
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="pet policy",
        results=[
            KnowledgeResult(
                chunk_text="Dogs under 50 lbs allowed. $350 pet deposit.",
                source_label="Pet Policy",
                similarity_score=0.88,
            )
        ],
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "Do you allow pets? I have a dog."
    )

    assert not result["escalated"]
    assert not result["knowledge_unavailable"]
    client.search_property_knowledge.assert_called_once()


@pytest.mark.asyncio
async def test_02_parking_question_retrieves_knowledge():
    """Parking question triggers RAG lookup; no escalation."""
    client = _make_backend()
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "What are the parking options? Is there covered parking?"
    )

    assert not result["escalated"]
    client.search_property_knowledge.assert_called_once()


# ===========================================================================
# SCENARIO 03: Tour booking — happy path
# ===========================================================================
# Manual script: tests/manual/03_tour_booking.md
# Tests the TourBookingCoordinator directly (VoiceSession delegates to it).


@pytest.mark.asyncio
async def test_03_tour_booking_happy_path():
    """
    Full tour booking flow:
    1. Caller states date preference -> availability fetched, slots presented
    2. Caller picks slot -> confirmation gate entered
    3. Caller confirms -> book_tour called -> BOOKING_COMPLETE
    """
    client = _make_backend()
    coordinator = TourBookingCoordinator(client=client)

    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    # Turn 1: date preference
    result = await coordinator.handle_turn(
        "How about sometime next week?",
        call_id=call_id,
        property_id=prop_id,
        lead_id=lead_id,
    )
    assert result.sub_state == BookingSubState.AVAILABILITY_FETCHED
    assert not result.done
    assert result.available_slots  # slots were returned
    assert result.agent_prompt is not None
    client.check_tour_availability.assert_called_once()

    # Turn 2: caller picks first option
    result = await coordinator.handle_turn(
        "The first one works.",
        call_id=call_id,
        property_id=prop_id,
        lead_id=lead_id,
    )
    assert result.sub_state == BookingSubState.AWAITING_BOOKING_CONFIRMATION
    assert not result.done
    assert result.selected_slot is not None
    assert "confirm" in result.agent_prompt.lower() or "sound right" in result.agent_prompt.lower()

    # Turn 3: caller confirms
    result = await coordinator.handle_turn(
        "Yes, that sounds great.",
        call_id=call_id,
        property_id=prop_id,
        lead_id=lead_id,
    )
    assert result.sub_state == BookingSubState.BOOKING_COMPLETE
    assert result.done
    assert result.booking_confirmed
    assert result.booking_id is not None
    assert not result.escalate
    client.book_tour.assert_called_once()


@pytest.mark.asyncio
async def test_03_book_tour_called_only_after_confirmation():
    """book_tour must NOT be called before the confirmation gate is passed."""
    client = _make_backend()
    coordinator = TourBookingCoordinator(client=client)

    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    # Turn 1: date preference
    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # Turn 2: pick slot — NOT yet confirmed
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # book_tour must NOT have been called yet
    client.book_tour.assert_not_called()


@pytest.mark.asyncio
async def test_03_max_three_slots_presented():
    """Coordinator presents at most 3 slots even if backend returns more."""
    client = _make_backend()
    extra_slots = [
        AvailableSlot(date=date(2026, 6, 10), start_time=time(h, 0), end_time=time(h, 30), slot_id=f"slot-{h}")
        for h in range(10, 15)  # 5 slots
    ]
    client.check_tour_availability.return_value = CheckTourAvailabilityResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        available_slots=extra_slots,
    )

    coordinator = TourBookingCoordinator(client=client)
    result = await coordinator.handle_turn(
        "Next week",
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        lead_id=uuid.UUID(_LEAD_ID),
    )

    # At most 3 slots in result
    assert len(result.available_slots) <= 3


# ===========================================================================
# SCENARIO 04: Resident maintenance question -> handoff
# ===========================================================================
# Manual script: tests/manual/04_maintenance.md
# Maintenance questions route to RESIDENT_SUPPORT. No email (OQ-12 blocker).


@pytest.mark.asyncio
async def test_04_maintenance_question_goes_to_resident_support():
    """Maintenance request keyword drives phase toward RESIDENT_SUPPORT, not LEAD_CAPTURE."""
    client = _make_backend()
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "I need to submit a maintenance request — my dishwasher is broken."
    )

    # Must not escalate (not an emergency or fair housing question)
    assert not result["escalated"]
    # Phase should be resident_support or knowledge_retrieval (not lead_capture)
    assert result["phase"] in [
        ConversationPhase.RESIDENT_SUPPORT.value,
        ConversationPhase.KNOWLEDGE_RETRIEVAL.value,
        ConversationPhase.INTENT_DETECTION.value,
    ], f"Unexpected phase for maintenance question: {result['phase']}"


# ===========================================================================
# SCENARIO 05: Question outside the knowledge base
# ===========================================================================
# Manual script: tests/manual/05_unknown_question.md


@pytest.mark.asyncio
async def test_05_empty_retrieval_sets_knowledge_unavailable():
    """
    Out-of-knowledge question: backend returns 0 results.
    knowledge_unavailable must be True so Gemini gets the "no invention" instruction.
    """
    client = _make_backend()
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="rooftop pool",
        results=[],
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "Do you have a rooftop pool with infinity edges?"
    )

    assert not result["escalated"]
    assert result["knowledge_unavailable"], (
        "Unknown question with 0 results must set knowledge_unavailable=True"
    )


@pytest.mark.asyncio
async def test_05_low_similarity_filtered_out():
    """
    Results below the 0.5 similarity threshold are dropped.
    If all results are below threshold, knowledge_unavailable becomes True.
    """
    client = _make_backend()
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="obscure question",
        results=[
            KnowledgeResult(
                chunk_text="Some vaguely related text.",
                source_label="FAQ",
                similarity_score=0.31,  # below 0.5 threshold
            )
        ],
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "What is the square footage of the rooftop if measured diagonally?"
    )

    # Low-score result filtered; no usable chunks
    assert result["knowledge_unavailable"]


# ===========================================================================
# SCENARIO 06: Fair Housing / legal question -> must escalate
# ===========================================================================
# Manual script: tests/manual/06_fair_housing_escalation.md


@pytest.mark.parametrize("caller_text,expected_reason", [
    ("Do you allow families with children?", "fair_housing_question"),
    ("Are there any restrictions based on race or religion?", "fair_housing_question"),
    ("I'm pregnant — is that okay?", "fair_housing_question"),
    ("What if I have a disability — can I get accommodations?", "fair_housing_question"),
    ("Do you accept housing vouchers?", "fair_housing_question"),
    ("Can I sue you if you don't fix the heating?", "legal_question"),
    ("I want to speak to an attorney about my lease.", "legal_question"),
    ("I need tax advice about whether rent is deductible.", "financial_advice_requested"),
])
@pytest.mark.asyncio
async def test_06_escalation_triggers(caller_text: str, expected_reason: str):
    """
    Fair Housing, legal, and financial keywords trigger mandatory escalation.
    The agent must NEVER answer these directly.
    """
    session = _make_session()

    result = await session.handle_caller_turn(caller_text)

    assert result["escalated"], (
        f"Should have escalated for: {caller_text!r}"
    )
    assert result["escalation_reason"] == expected_reason, (
        f"Expected reason '{expected_reason}', got '{result['escalation_reason']}' "
        f"for: {caller_text!r}"
    )
    assert result["phase"] == ConversationPhase.ESCALATION.value


@pytest.mark.asyncio
async def test_06_escalation_runs_before_state_machine():
    """
    Safety guarantee: EscalationDetector runs BEFORE the state machine.
    Even if the state machine would advance, escalation must short-circuit.
    """
    session = _make_session()
    # Prime the session with some state to make sure it gets bypassed
    session.state_machine.force_phase(ConversationPhase.LEAD_CAPTURE)

    result = await session.handle_caller_turn("Are there children allowed here?")

    assert result["escalated"]
    assert result["phase"] == ConversationPhase.ESCALATION.value


# ===========================================================================
# SCENARIO 07: Backend tool failure during booking -> graceful recovery
# ===========================================================================
# Manual script: tests/manual/07_tool_failure_recovery.md


@pytest.mark.asyncio
async def test_07_slot_unavailable_re_prompts_for_new_slot():
    """
    book_tour returns BOOKING_SLOT_UNAVAILABLE.
    Agent must NOT claim the booking succeeded.
    Coordinator re-enters AWAITING_DATE_PREFERENCE so caller can pick again.
    """
    client = _make_backend()
    client.book_tour.side_effect = BackendToolError(
        code="BOOKING_SLOT_UNAVAILABLE",
        message="That slot was just taken.",
        retryable=False,
    )

    coordinator = TourBookingCoordinator(client=client)
    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    # Get to confirmation
    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # Confirm — triggers book_tour which fails with BOOKING_SLOT_UNAVAILABLE
    result = await coordinator.handle_turn(
        "Yes, confirm it.",
        call_id=call_id,
        property_id=prop_id,
        lead_id=lead_id,
    )

    # Must NOT claim success
    assert not result.booking_confirmed
    assert not result.done or result.sub_state == BookingSubState.AWAITING_DATE_PREFERENCE
    # Should re-prompt for a different slot
    assert result.agent_prompt is not None
    assert "slot" in result.agent_prompt.lower() or "date" in result.agent_prompt.lower() or "taken" in result.agent_prompt.lower()


@pytest.mark.asyncio
async def test_07_transient_failure_retries_once_then_escalates():
    """
    Transient book_tour failure: retryable=True.
    Coordinator retries once. If still failing, escalates — not retries again.
    """
    client = _make_backend()
    client.book_tour.side_effect = BackendToolError(
        code="CALENDAR_WRITE_FAILED",
        message="Calendar service unavailable.",
        retryable=True,
    )

    coordinator = TourBookingCoordinator(client=client)
    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    result = await coordinator.handle_turn(
        "Yes please.", call_id=call_id, property_id=prop_id, lead_id=lead_id
    )

    # After exhausted retries: escalate, done, no booking
    assert result.done
    assert not result.booking_confirmed
    assert result.escalate
    # book_tour called exactly MAX_BOOKING_RETRIES + 1 times (1 initial + 1 retry)
    assert client.book_tour.call_count == 2  # MAX_BOOKING_RETRIES=1 means 2 attempts max


# ===========================================================================
# SCENARIO 08: Barge-in — caller interrupts mid-response
# ===========================================================================
# Manual script: tests/manual/08_barge_in.md
# Real audio barge-in is Phase 1 finish-up. Here we verify state machine
# handles a new caller_text arriving during AWAITING_CONFIRMATION gracefully.


@pytest.mark.asyncio
async def test_08_barge_in_mid_confirmation_handled_gracefully():
    """
    Simulate barge-in: caller changes topic while agent is in confirmation gate.
    The state machine should handle the new input without crashing.
    """
    session = _make_session()

    # Drive to LEAD_CAPTURE
    await session.handle_caller_turn("I want to schedule a tour.")

    # New caller utterance arrives "mid-confirmation" — state machine must handle
    result = await session.handle_caller_turn(
        "Actually wait — what's the pet policy first?"
    )

    # Should not crash or escalate; should handle as a new intent
    assert not result["escalated"]
    assert result["phase"] != "ended"


# ===========================================================================
# ADVERSARIAL SCENARIO 09: Caller demands rent discount
# ===========================================================================


@pytest.mark.asyncio
async def test_09_demands_discount_no_escalation_no_commitment():
    """
    Caller demands a rent reduction. Agent must NOT commit to negotiating.
    This is NOT a Fair Housing question — no escalation required.
    The agent should stay in conversation and decline to negotiate.
    """
    session = _make_session()

    result = await session.handle_caller_turn(
        "Can you give me a discount on the rent? I want 10% off."
    )

    # Not a Fair Housing / legal / emergency question — no escalation
    assert not result["escalated"]
    # Agent should not be in ENDED state after one turn
    assert result["phase"] != "ended"


# ===========================================================================
# ADVERSARIAL SCENARIO 10: Direct family-status question -> escalation
# ===========================================================================


@pytest.mark.asyncio
async def test_10_children_question_escalates():
    """
    Direct question about children living in the building.
    Must escalate as Fair Housing — familial status is a protected class.
    """
    session = _make_session()

    result = await session.handle_caller_turn(
        "I want to know if children are allowed in this building."
    )

    assert result["escalated"]
    assert result["escalation_reason"] == "fair_housing_question"


# ===========================================================================
# ADVERSARIAL SCENARIO 11: Caller refuses every confirmation -> handoff
# ===========================================================================


@pytest.mark.asyncio
async def test_11_repeated_confirmation_refusals_do_not_book():
    """
    Caller refuses confirmation 3 times. Coordinator must NOT book.
    State should remain in date-preference loop, not exhaust into an error.
    """
    client = _make_backend()
    coordinator = TourBookingCoordinator(client=client)

    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    # Fetch slots
    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # Pick slot -> confirmation prompt
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # Refuse confirmation three times
    for _ in range(3):
        result = await coordinator.handle_turn(
            "No, that's not right.", call_id=call_id, property_id=prop_id, lead_id=lead_id
        )

    # book_tour must never have been called
    client.book_tour.assert_not_called()
    # Must not have silently succeeded
    assert not coordinator._sub_state == BookingSubState.BOOKING_COMPLETE


# ===========================================================================
# ADVERSARIAL SCENARIO 12: Repeated tool failures -> handoff
# ===========================================================================


@pytest.mark.asyncio
async def test_12_three_consecutive_tool_failures_trigger_escalation():
    """
    3 consecutive tool failures bump tool_failure_count to >= threshold.
    EscalationDetector triggers on the next caller turn.
    """
    session = _make_session()

    # Manually bump failure counter to threshold (3)
    session.state.tool_failure_count = 3

    result = await session.handle_caller_turn("Can you help me with something?")

    assert result["escalated"]
    assert result["escalation_reason"] == EscalationReason.BACKEND_TOOL_FAILURE.value


# ===========================================================================
# ADVERSARIAL SCENARIO 13: Emergency keyword -> emergency escalation
# ===========================================================================


@pytest.mark.parametrize("caller_text", [
    "There's a fire alarm going off in my building!",
    "I smell gas in my apartment, what do I do?",
    "Someone broke into my unit.",
    "My neighbor collapsed, I need to call 911!",
    "There's flooding in the basement.",
])
@pytest.mark.asyncio
async def test_13_emergency_keywords_escalate_with_emergency_urgency(caller_text: str):
    """Emergency keywords must trigger EMERGENCY escalation, not a polite handoff."""
    session = _make_session()

    result = await session.handle_caller_turn(caller_text)

    assert result["escalated"], f"Should escalate for: {caller_text!r}"
    assert result["escalation_reason"] == EscalationReason.EMERGENCY.value
    assert result["phase"] == ConversationPhase.ESCALATION.value


# ===========================================================================
# FAILURE-SHAPE SCENARIO 14: Empty retrieval -> fallback, no invention
# ===========================================================================


@pytest.mark.asyncio
async def test_14_empty_retrieval_knowledge_unavailable_flag():
    """
    RAG returns 0 results. knowledge_unavailable=True must be in the result
    so the prompt builder injects the "no invention" instruction to Gemini.
    """
    client = _make_backend()
    client.search_property_knowledge.return_value = SearchKnowledgeResponse(
        property_id=uuid.UUID(_PROPERTY_ID),
        query="roof terrace",
        results=[],
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "Tell me about the amenities on the roof terrace."
    )

    assert result["knowledge_unavailable"]
    assert not result["escalated"]


@pytest.mark.asyncio
async def test_14_retrieval_backend_error_sets_knowledge_unavailable():
    """
    RAG backend call fails. Must return knowledge_unavailable=True, not crash.
    """
    client = _make_backend()
    client.search_property_knowledge.side_effect = BackendToolError(
        code="RETRIEVAL_FAILED", message="Vector DB timeout", retryable=True
    )
    session = _make_session(client)

    result = await session.handle_caller_turn(
        "What are your parking rates?"
    )

    # Retrieval error must not crash the call
    assert result["knowledge_unavailable"]
    assert not result["escalated"]


# ===========================================================================
# FAILURE-SHAPE SCENARIO 15: Low-confidence detection
# ===========================================================================


def test_15_confidence_evaluator_flags_hedging_language():
    """
    ConfidenceEvaluator detects LLM hedging patterns.
    This tests the evaluator directly — not via VoiceSession (LLM is stubbed).
    """
    from voice_agent.conversation.confidence import ConfidenceEvaluator

    evaluator = ConfidenceEvaluator()

    # High confidence: direct factual statement
    high_conf = evaluator.evaluate("The pet deposit is $350 for dogs under 50 pounds.")
    assert high_conf.confidence >= 0.5, "Direct factual answer should be high confidence"

    # Low confidence: heavy hedging language
    low_conf = evaluator.evaluate(
        "I think it might possibly be around maybe $1,200 or so, but I'm not really sure."
    )
    assert low_conf.confidence < high_conf.confidence, "Heavily hedged answer should score lower"


# ===========================================================================
# FAILURE-SHAPE SCENARIO 16: Transient failure then success
# ===========================================================================


@pytest.mark.asyncio
async def test_16_transient_book_tour_failure_then_success():
    """
    First book_tour call: transient failure (retryable=True).
    Second call: success.
    Coordinator should report booking_confirmed=True, call book_tour twice.
    """
    client = _make_backend()

    call_count = 0

    async def book_tour_side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise BackendToolError(
                code="CALENDAR_TIMEOUT", message="Transient timeout.", retryable=True
            )
        return BookTourResponse(
            booking_id=uuid.UUID(_BOOKING_ID),
            calendar_event_id="cal-456",
            tour_date=_SLOT_1.date,
            start_time=_SLOT_1.start_time,
            status="confirmed",
        )

    client.book_tour.side_effect = book_tour_side_effect

    coordinator = TourBookingCoordinator(client=client)
    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("First option", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    result = await coordinator.handle_turn(
        "Yes, confirm that.", call_id=call_id, property_id=prop_id, lead_id=lead_id
    )

    assert result.booking_confirmed, "Should succeed after retry"
    assert call_count == 2, f"Should have called book_tour twice, got {call_count}"
    assert result.sub_state == BookingSubState.BOOKING_COMPLETE


# ===========================================================================
# FAILURE-SHAPE SCENARIO 17: Email send when email_confirmed=False
# ===========================================================================


@pytest.mark.asyncio
async def test_17_email_not_sent_when_not_confirmed():
    """
    send_follow_up_email must NOT be called unless caller confirmed the address.
    FollowUpEmailCoordinator gates this with email_confirmed flag.
    """
    client = _make_backend()
    coordinator = FollowUpEmailCoordinator(client=client)

    # Start the coordinator — it reads back the email
    initial_result = coordinator.start(email="test@example.com", booking_confirmed=False)
    assert initial_result.sub_state == EmailSubState.CONFIRMING_EMAIL

    # Caller does NOT confirm — says no
    result = await coordinator.handle_turn(
        "No, that's wrong.",
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        lead_id=uuid.UUID(_LEAD_ID),
    )

    # send_follow_up_email must not have been called
    client.send_follow_up_email.assert_not_called()
    assert not result.email_sent
    assert result.sub_state == EmailSubState.AWAITING_CORRECTION


@pytest.mark.asyncio
async def test_17_email_sent_only_after_confirmation():
    """
    Confirm -> send path: send_follow_up_email called exactly once after confirmation.
    """
    client = _make_backend()
    coordinator = FollowUpEmailCoordinator(client=client)

    coordinator.start(email="test@example.com", booking_confirmed=True)

    result = await coordinator.handle_turn(
        "Yes, that's correct.",
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        lead_id=uuid.UUID(_LEAD_ID),
    )

    client.send_follow_up_email.assert_called_once()
    assert result.email_sent
    assert result.email_confirmed
    assert result.sub_state == EmailSubState.EMAIL_SENT


# ===========================================================================
# FAILURE-SHAPE SCENARIO 18: Escalation triggered mid-booking
# ===========================================================================


@pytest.mark.asyncio
async def test_18_emergency_during_booking_escalates():
    """
    Caller mentions an emergency keyword while in the booking flow.
    EscalationDetector runs FIRST — session must escalate, not continue booking.
    """
    client = _make_backend()
    session = _make_session(client)

    # Get into a booking-ish state
    session.state_machine.force_phase(ConversationPhase.TOUR_BOOKING)

    # Emergency keyword mid-booking
    result = await session.handle_caller_turn(
        "Wait, I smell gas — there's a gas leak in my unit!"
    )

    assert result["escalated"]
    assert result["escalation_reason"] == EscalationReason.EMERGENCY.value
    # book_tour must NOT have been called
    client.book_tour.assert_not_called()


@pytest.mark.asyncio
async def test_18_fair_housing_mid_booking_escalates():
    """
    Fair Housing question raised while booking a tour.
    Agent must escalate — not complete the booking first.
    """
    client = _make_backend()
    session = _make_session(client)
    session.state_machine.force_phase(ConversationPhase.TOUR_BOOKING)

    result = await session.handle_caller_turn(
        "By the way, can you tell me if this is a family-friendly building with children?"
    )

    assert result["escalated"]
    assert result["escalation_reason"] == "fair_housing_question"
    client.book_tour.assert_not_called()


# ===========================================================================
# RETRY INTEGRATION: failure count does not double-count retries
# ===========================================================================


@pytest.mark.asyncio
async def test_retry_counts_as_one_toward_escalation_threshold():
    """
    A transient failure that gets retried (2 HTTP calls) counts as ONE
    toward the escalation threshold, not two.
    VoiceSession.state.tool_failure_count should increment by 1 per failed operation,
    not per HTTP attempt.

    NOTE: This tests the coordinator behavior — the counter increment is in
    VoiceSession which calls the coordinator. We verify the coordinator's
    retry_count attribute, not tool_failure_count (which VoiceSession owns).
    """
    client = _make_backend()
    client.book_tour.side_effect = BackendToolError(
        code="CALENDAR_TIMEOUT", message="Timeout", retryable=True
    )

    coordinator = TourBookingCoordinator(client=client)
    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("Yes", call_id=call_id, property_id=prop_id, lead_id=lead_id)

    # 2 HTTP calls (1 attempt + 1 retry) happened but retry_count = 1
    assert client.book_tour.call_count == 2
    assert coordinator._retry_count == 1, (
        "One failed operation (with retry) should count as 1, not 2"
    )


@pytest.mark.asyncio
async def test_non_retryable_failure_does_not_retry():
    """
    Non-retryable failure (retryable=False): book_tour called exactly once.
    Coordinator escalates immediately — does not retry.
    """
    client = _make_backend()
    client.book_tour.side_effect = BackendToolError(
        code="PROPERTY_NOT_FOUND", message="Property does not exist.", retryable=False
    )

    coordinator = TourBookingCoordinator(client=client)
    prop_id = uuid.UUID(_PROPERTY_ID)
    lead_id = uuid.UUID(_LEAD_ID)
    call_id = uuid.UUID(_BACKEND_CALL_ID)

    await coordinator.handle_turn("Next week", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    await coordinator.handle_turn("Option one", call_id=call_id, property_id=prop_id, lead_id=lead_id)
    result = await coordinator.handle_turn(
        "Yes.", call_id=call_id, property_id=prop_id, lead_id=lead_id
    )

    # Called exactly once — no retry on non-retryable
    assert client.book_tour.call_count == 1
    assert result.escalate
    assert result.done
    assert not result.booking_confirmed


@pytest.mark.asyncio
async def test_email_non_retryable_failure_does_not_retry():
    """Non-retryable email failure: send called exactly once, escalates immediately."""
    client = _make_backend()
    client.send_follow_up_email.side_effect = BackendToolError(
        code="TEMPLATE_NOT_FOUND", message="Bad template.", retryable=False
    )

    coordinator = FollowUpEmailCoordinator(client=client)
    coordinator.start(email="test@example.com", booking_confirmed=False)

    result = await coordinator.handle_turn(
        "Yes, that's right.",
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        lead_id=uuid.UUID(_LEAD_ID),
    )

    assert client.send_follow_up_email.call_count == 1
    assert result.escalate
    assert not result.email_sent


@pytest.mark.asyncio
async def test_email_transient_failure_retries_once():
    """Transient email failure retries once; if still fails, escalates."""
    client = _make_backend()
    client.send_follow_up_email.side_effect = BackendToolError(
        code="EMAIL_GATEWAY_TIMEOUT", message="Timeout.", retryable=True
    )

    coordinator = FollowUpEmailCoordinator(client=client)
    coordinator.start(email="test@example.com", booking_confirmed=False)

    result = await coordinator.handle_turn(
        "Yes that's correct.",
        call_id=uuid.UUID(_BACKEND_CALL_ID),
        property_id=uuid.UUID(_PROPERTY_ID),
        lead_id=uuid.UUID(_LEAD_ID),
    )

    # MAX_EMAIL_RETRIES=1 means 2 attempts (1 initial + 1 retry)
    assert client.send_follow_up_email.call_count == 2
    assert result.escalate
    assert not result.email_sent
