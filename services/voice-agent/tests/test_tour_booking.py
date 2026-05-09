"""
Phase 4 tests — TourBookingCoordinator.

Covers all spec deliverables from §8:
  1. Happy path: date preference → availability fetch → slot present → confirm → book_tour called
  2. Caller refuses presented slots → re-fetch with new window
  3. check_tour_availability returns empty → agent offers callback / handoff
  4. book_tour returns BOOKING_SLOT_UNAVAILABLE (retryable=False) → re-prompt for new slot
  5. book_tour returns transient error (retryable=True) → retry once, then offer handoff

Also covers:
  - State machine TOUR_BOOKING phase
  - try_enter_tour_booking eligibility gate
  - VoiceSession.handle_caller_turn delegation when in TOUR_BOOKING phase
  - Confirmation gate: refusal returns to AWAITING_DATE_PREFERENCE
  - format_slot output
  - _parse_date_window heuristics
"""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from unittest.mock import AsyncMock, call

import pytest

from voice_agent.conversation.booking import (
    BookingSubState,
    TourBookingCoordinator,
    _format_slot,
    _parse_date_window,
)
from voice_agent.conversation.state_machine import ConversationPhase, LeadCaptureStateMachine
from voice_agent.tools.backend_client import (
    AvailableSlot,
    BackendClient,
    BackendToolError,
    BookTourResponse,
    CallCreateResponse,
    CheckTourAvailabilityResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PROP_ID = uuid.uuid4()
_CALL_ID = uuid.uuid4()
_LEAD_ID = uuid.uuid4()

_TODAY = date.today()


def _make_slot(offset_days: int = 1, slot_id: str = "slot-001") -> AvailableSlot:
    return AvailableSlot(
        date=_TODAY + timedelta(days=offset_days),
        start_time=time(10, 0),
        end_time=time(11, 0),
        slot_id=slot_id,
    )


def _make_avail_response(slots: list[AvailableSlot]) -> CheckTourAvailabilityResponse:
    return CheckTourAvailabilityResponse(
        property_id=_PROP_ID,
        available_slots=slots,
    )


def _make_booking_response(slot: AvailableSlot) -> BookTourResponse:
    return BookTourResponse(
        booking_id=uuid.uuid4(),
        calendar_event_id="CAL-123",
        tour_date=slot.date,
        start_time=slot.start_time,
        status="confirmed",
    )


def _make_client(
    avail_response: CheckTourAvailabilityResponse | None = None,
    book_response: BookTourResponse | None = None,
    book_side_effect: Exception | None = None,
) -> AsyncMock:
    client = AsyncMock(spec=BackendClient)
    if avail_response is not None:
        client.check_tour_availability.return_value = avail_response
    if book_response is not None:
        client.book_tour.return_value = book_response
    if book_side_effect is not None:
        client.book_tour.side_effect = book_side_effect
    client.create_call_event.return_value = None
    return client


# ===========================================================================
# Section 1 — Happy path: full booking flow
# ===========================================================================


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_happy_path_full_booking(self) -> None:
        """
        Deliverable 1: Full happy path.
        Turn 1: caller states date preference -> slots presented.
        Turn 2: caller picks a slot.
        Turn 3: caller confirms.
        -> book_tour called with correct slot.
        """
        slot = _make_slot(offset_days=2, slot_id="slot-007")
        client = _make_client(
            avail_response=_make_avail_response([slot]),
            book_response=_make_booking_response(slot),
        )
        coord = TourBookingCoordinator(client=client)

        # Turn 1: date preference
        result1 = await coord.handle_turn("next week", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result1.sub_state == BookingSubState.AVAILABILITY_FETCHED
        assert result1.available_slots != []
        assert not result1.done
        client.check_tour_availability.assert_called_once()

        # Turn 2: pick the first slot
        result2 = await coord.handle_turn("option 1", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result2.sub_state == BookingSubState.AWAITING_BOOKING_CONFIRMATION
        assert "confirm" in result2.agent_prompt.lower() or "confirm" in result2.agent_prompt
        assert not result2.done

        # Turn 3: confirm
        result3 = await coord.handle_turn("yes, that works", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result3.sub_state == BookingSubState.BOOKING_COMPLETE
        assert result3.done
        assert result3.booking_confirmed
        assert result3.booking_id is not None

        # book_tour called exactly once with correct slot
        client.book_tour.assert_called_once()
        call_kwargs = client.book_tour.call_args.kwargs
        assert call_kwargs["property_id"] == _PROP_ID
        assert call_kwargs["lead_id"] == _LEAD_ID
        assert call_kwargs["selected_slot"].slot_id == "slot-007"

    @pytest.mark.asyncio
    async def test_second_slot_selected(self) -> None:
        """Caller picks 'option 2' from the list."""
        slot1 = _make_slot(offset_days=1, slot_id="slot-A")
        slot2 = _make_slot(offset_days=2, slot_id="slot-B")
        client = _make_client(
            avail_response=_make_avail_response([slot1, slot2]),
            book_response=_make_booking_response(slot2),
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("this week", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("option 2", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.booking_confirmed
        book_kwargs = client.book_tour.call_args.kwargs
        assert book_kwargs["selected_slot"].slot_id == "slot-B"


# ===========================================================================
# Section 2 — Caller refuses presented slots
# ===========================================================================


class TestCallerRefusesSlots:
    @pytest.mark.asyncio
    async def test_caller_requests_different_dates(self) -> None:
        """
        Deliverable 2: Caller says "different dates" → re-enter AWAITING_DATE_PREFERENCE.
        """
        slot = _make_slot()
        client = _make_client(avail_response=_make_avail_response([slot]))
        coord = TourBookingCoordinator(client=client)

        # Get to AVAILABILITY_FETCHED
        await coord.handle_turn("tomorrow", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert coord.sub_state == BookingSubState.AVAILABILITY_FETCHED

        # Caller rejects
        result = await coord.handle_turn("none of those work, different date please", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.sub_state == BookingSubState.AWAITING_DATE_PREFERENCE
        assert not result.done

    @pytest.mark.asyncio
    async def test_caller_refuses_confirmation_returns_to_date_preference(self) -> None:
        """Caller declines at confirmation gate → return to AWAITING_DATE_PREFERENCE."""
        slot = _make_slot(slot_id="slot-X")
        client = _make_client(
            avail_response=_make_avail_response([slot]),
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("this weekend", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("option 1", _CALL_ID, _PROP_ID, _LEAD_ID)

        # Refuse confirmation
        result = await coord.handle_turn("no, that's not right", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.sub_state == BookingSubState.AWAITING_DATE_PREFERENCE
        assert not result.done
        # book_tour must NOT have been called
        client.book_tour.assert_not_called()


# ===========================================================================
# Section 3 — Empty availability
# ===========================================================================


class TestEmptyAvailability:
    @pytest.mark.asyncio
    async def test_no_slots_triggers_handoff(self) -> None:
        """
        Deliverable 3: Empty slots → done=True, escalate=True, no booking attempted.
        """
        client = _make_client(avail_response=_make_avail_response([]))
        coord = TourBookingCoordinator(client=client)

        result = await coord.handle_turn("this week", _CALL_ID, _PROP_ID, _LEAD_ID)
        assert result.done
        assert result.escalate
        assert not result.booking_confirmed
        client.book_tour.assert_not_called()
        assert "don't see" in result.agent_prompt.lower() or "team" in result.agent_prompt.lower()

    @pytest.mark.asyncio
    async def test_availability_backend_error_triggers_handoff(self) -> None:
        """check_tour_availability failure → done=True, escalate=True."""
        client = AsyncMock(spec=BackendClient)
        client.check_tour_availability.side_effect = BackendToolError(
            code="TIMEOUT", message="Timed out", retryable=True
        )
        client.create_call_event.return_value = None

        coord = TourBookingCoordinator(client=client)
        result = await coord.handle_turn("tomorrow", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.done
        assert result.escalate
        assert not result.booking_confirmed


# ===========================================================================
# Section 4 — BOOKING_SLOT_UNAVAILABLE (non-retryable)
# ===========================================================================


class TestSlotUnavailable:
    @pytest.mark.asyncio
    async def test_slot_unavailable_re_prompts_for_new_slot(self) -> None:
        """
        Deliverable 4: BOOKING_SLOT_UNAVAILABLE → re-enter AWAITING_DATE_PREFERENCE.
        Agent tells caller the slot was taken; asks for new preference.
        """
        slot = _make_slot(slot_id="slot-gone")
        client = _make_client(avail_response=_make_avail_response([slot]))
        client.book_tour.side_effect = BackendToolError(
            code="BOOKING_SLOT_UNAVAILABLE",
            message="Slot no longer available",
            retryable=False,
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("tomorrow", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("first one", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.sub_state == BookingSubState.AWAITING_DATE_PREFERENCE
        assert not result.done
        assert not result.booking_confirmed
        assert not result.escalate
        assert "taken" in result.agent_prompt.lower() or "other" in result.agent_prompt.lower()

    @pytest.mark.asyncio
    async def test_slot_unavailable_does_not_claim_booking_succeeded(self) -> None:
        """Safety: booking_confirmed must be False on BOOKING_SLOT_UNAVAILABLE."""
        slot = _make_slot()
        client = _make_client(avail_response=_make_avail_response([slot]))
        client.book_tour.side_effect = BackendToolError(
            code="BOOKING_SLOT_UNAVAILABLE", message="Gone", retryable=False
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("this week", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("option 1", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert not result.booking_confirmed


# ===========================================================================
# Section 5 — Transient error (retryable=True)
# ===========================================================================


class TestTransientError:
    @pytest.mark.asyncio
    async def test_retryable_error_retries_once_then_escalates(self) -> None:
        """
        Deliverable 5: Transient error → retry once → if still failing → escalate.
        """
        slot = _make_slot(slot_id="slot-retry")
        client = _make_client(avail_response=_make_avail_response([slot]))
        # Always fail with retryable error
        client.book_tour.side_effect = BackendToolError(
            code="SERVICE_UNAVAILABLE",
            message="Temporarily unavailable",
            retryable=True,
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("this week", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("option 1", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.done
        assert result.escalate
        assert not result.booking_confirmed
        # book_tour attempted twice (1 attempt + 1 retry)
        assert client.book_tour.call_count == 2

    @pytest.mark.asyncio
    async def test_retryable_error_succeeds_on_second_attempt(self) -> None:
        """If retry succeeds, booking_confirmed=True and call count == 2."""
        slot = _make_slot(slot_id="slot-success")
        client = _make_client(avail_response=_make_avail_response([slot]))
        # Fail first, succeed second
        client.book_tour.side_effect = [
            BackendToolError("TIMEOUT", "Timed out", retryable=True),
            _make_booking_response(slot),
        ]
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("tomorrow", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("option 1", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.booking_confirmed
        assert result.done
        assert client.book_tour.call_count == 2

    @pytest.mark.asyncio
    async def test_non_retryable_error_escalates_immediately(self) -> None:
        """Non-retryable failure → escalate, no retry."""
        slot = _make_slot()
        client = _make_client(avail_response=_make_avail_response([slot]))
        client.book_tour.side_effect = BackendToolError(
            code="LEAD_NOT_FOUND", message="Lead missing", retryable=False
        )
        coord = TourBookingCoordinator(client=client)

        await coord.handle_turn("this week", _CALL_ID, _PROP_ID, _LEAD_ID)
        await coord.handle_turn("first", _CALL_ID, _PROP_ID, _LEAD_ID)
        result = await coord.handle_turn("yes", _CALL_ID, _PROP_ID, _LEAD_ID)

        assert result.escalate
        assert not result.booking_confirmed
        assert client.book_tour.call_count == 1  # no retry for non-retryable


# ===========================================================================
# Section 6 — State machine TOUR_BOOKING phase and eligibility gate
# ===========================================================================


class TestStateMachineTourBookingPhase:
    def test_tour_booking_phase_in_enum(self) -> None:
        assert ConversationPhase.TOUR_BOOKING.value == "tour_booking"

    def test_email_followup_phase_in_enum(self) -> None:
        assert ConversationPhase.EMAIL_FOLLOWUP.value == "email_followup"

    def test_try_enter_tour_booking_eligible(self) -> None:
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.LEAD_CAPTURE)
        sm.captured_fields.set("name", "Alice")
        sm.captured_fields.set("phone", "555-0100")
        result = sm.try_enter_tour_booking()
        assert result is True
        assert sm.phase == ConversationPhase.TOUR_BOOKING

    def test_try_enter_tour_booking_ineligible_no_name(self) -> None:
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.LEAD_CAPTURE)
        sm.captured_fields.set("phone", "555-0100")
        result = sm.try_enter_tour_booking()
        assert result is False

    def test_try_enter_tour_booking_ineligible_wrong_phase(self) -> None:
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.RESIDENT_SUPPORT)
        sm.captured_fields.set("name", "Alice")
        sm.captured_fields.set("phone", "555-0100")
        result = sm.try_enter_tour_booking()
        assert result is False

    def test_try_enter_tour_booking_name_and_email_sufficient(self) -> None:
        """name + email (no phone) is eligible."""
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.LEAD_CAPTURE)
        sm.captured_fields.set("name", "Bob")
        sm.captured_fields.set("email", "bob@example.com")
        assert sm.try_enter_tour_booking() is True


# ===========================================================================
# Section 7 — Utility functions
# ===========================================================================


class TestUtilities:
    def test_format_slot(self) -> None:
        slot = _make_slot(offset_days=0)
        formatted = _format_slot(slot)
        # Should contain "10:00 AM" and a day name
        assert "10:00 AM" in formatted or "10:00 AM" in formatted.replace(" ", " ")

    def test_parse_date_window_this_week(self) -> None:
        start, end = _parse_date_window("I'd like to come this week")
        today = date.today()
        assert start >= today
        assert (end - start).days <= 7

    def test_parse_date_window_tomorrow(self) -> None:
        start, end = _parse_date_window("How about tomorrow?")
        today = date.today()
        assert start == today + timedelta(days=1)

    def test_parse_date_window_fallback(self) -> None:
        start, end = _parse_date_window("I have no preference, any time")
        today = date.today()
        assert start == today
        assert end == today + timedelta(days=7)


# ===========================================================================
# Section 8 — VoiceSession integration: TOUR_BOOKING delegation
# ===========================================================================


class TestVoiceSessionTourBookingIntegration:
    """
    Verify VoiceSession.handle_caller_turn() correctly delegates to
    TourBookingCoordinator when phase == TOUR_BOOKING and surfaces booking
    result keys in the return dict.
    """

    def _make_session(self):
        from voice_agent.agent.session import VoiceSession

        prop_id = str(uuid.uuid4())
        backend_call_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())

        slot = _make_slot(slot_id="session-slot-1")
        client = AsyncMock(spec=BackendClient)
        client.create_call.return_value = CallCreateResponse(
            id=uuid.UUID(backend_call_id),
            property_id=uuid.UUID(prop_id),
            status="active",
        )
        client.check_tour_availability.return_value = _make_avail_response([slot])
        client.book_tour.return_value = _make_booking_response(slot)
        client.create_call_event.return_value = None
        client.search_property_knowledge.return_value = None  # not relevant here

        session = VoiceSession(
            property_id=prop_id,
            jwt_token="eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig",
            backend_client=client,
        )
        session.state.backend_call_id = backend_call_id
        session.state.lead_id = lead_id
        # Force state machine into TOUR_BOOKING
        session.state_machine.force_phase(ConversationPhase.TOUR_BOOKING)

        return session, client, slot

    @pytest.mark.asyncio
    async def test_booking_date_preference_turn_returns_booking_sub_state(self) -> None:
        session, client, _ = self._make_session()
        result = await session.handle_caller_turn("this week")
        assert "booking_sub_state" in result
        assert result["booking_sub_state"] == BookingSubState.AVAILABILITY_FETCHED.value

    @pytest.mark.asyncio
    async def test_full_booking_flow_via_session(self) -> None:
        session, client, slot = self._make_session()

        # Turn 1: date
        r1 = await session.handle_caller_turn("next week")
        assert r1["booking_sub_state"] == BookingSubState.AVAILABILITY_FETCHED.value

        # Turn 2: pick slot
        r2 = await session.handle_caller_turn("option 1")
        assert r2["booking_sub_state"] == BookingSubState.AWAITING_BOOKING_CONFIRMATION.value

        # Turn 3: confirm
        r3 = await session.handle_caller_turn("yes, book it")
        assert r3["booking_confirmed"] is True
        # After booking, session should have moved to CLOSING
        assert r3.get("phase") == ConversationPhase.CLOSING.value or \
               session.state_machine.phase == ConversationPhase.CLOSING

    @pytest.mark.asyncio
    async def test_empty_slots_escalates_via_session(self) -> None:
        from voice_agent.agent.session import VoiceSession

        prop_id = str(uuid.uuid4())
        backend_call_id = str(uuid.uuid4())
        lead_id = str(uuid.uuid4())

        client = AsyncMock(spec=BackendClient)
        client.create_call.return_value = CallCreateResponse(
            id=uuid.UUID(backend_call_id),
            property_id=uuid.UUID(prop_id),
            status="active",
        )
        client.check_tour_availability.return_value = _make_avail_response([])
        client.create_call_event.return_value = None
        client.search_property_knowledge.return_value = None

        session = VoiceSession(
            property_id=prop_id,
            jwt_token="eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoidGVzdCJ9.fake_sig",
            backend_client=client,
        )
        session.state.backend_call_id = backend_call_id
        session.state.lead_id = lead_id
        session.state_machine.force_phase(ConversationPhase.TOUR_BOOKING)

        result = await session.handle_caller_turn("this week please")
        assert result.get("booking_escalate") is True or result.get("escalated") is True
