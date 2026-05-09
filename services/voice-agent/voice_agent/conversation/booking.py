"""
TourBookingCoordinator — manages the TOUR_BOOKING phase sub-state machine.

The coordinator is entered from LEAD_CAPTURE when:
  - Caller has expressed tour interest (tour_interest=True)  AND
    has minimum required fields (name + phone OR email), OR
  - Caller explicitly asks to book a tour at any point.

Sub-state machine inside TOUR_BOOKING:
  AWAITING_DATE_PREFERENCE  -> caller suggests a date window
  AVAILABILITY_FETCHED      -> agent has fetched and presented slots
  SLOT_SELECTED             -> caller has picked a specific slot
  AWAITING_BOOKING_CONFIRMATION -> confirmation gate (same pattern as state_machine.py)
  BOOKING_COMPLETE          -> book_tour succeeded; hand back to VoiceSession
  BOOKING_FAILED            -> non-retryable failure; hand back for escalation
  AWAITING_RETRY_SLOT       -> retryable failure; re-enter AWAITING_DATE_PREFERENCE

Safety invariants (enforced here, not just in prompts):
  - book_tour is NEVER called unless caller has confirmed the slot.
  - If book_tour fails, we NEVER claim the booking succeeded.
  - After a retryable failure we retry once; then escalate.
  - Empty availability -> offer callback / escalate; never invent slots.

Usage in VoiceSession.handle_caller_turn():

    if self.state_machine.phase == ConversationPhase.TOUR_BOOKING:
        booking_result = await self._booking_coordinator.handle_turn(
            caller_text=caller_text,
            call_id=self._effective_call_id(),
            property_id=uuid.UUID(self.state.property_id),
            lead_id=uuid.UUID(self.state.lead_id),
        )
        # booking_result.done signals VoiceSession to exit TOUR_BOOKING
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from voice_agent.tools.backend_client import (
        AvailableSlot,
        BackendClient,
        BookTourResponse,
    )

log = logging.getLogger("voice_agent.conversation.booking")

# ---------------------------------------------------------------------------
# Sub-states
# ---------------------------------------------------------------------------

MAX_BOOKING_RETRIES: int = 1


class BookingSubState(str, Enum):
    AWAITING_DATE_PREFERENCE = "awaiting_date_preference"
    AVAILABILITY_FETCHED = "availability_fetched"
    SLOT_SELECTED = "slot_selected"
    AWAITING_BOOKING_CONFIRMATION = "awaiting_booking_confirmation"
    BOOKING_COMPLETE = "booking_complete"
    BOOKING_FAILED = "booking_failed"    # non-retryable
    AWAITING_RETRY_SLOT = "awaiting_retry_slot"


# ---------------------------------------------------------------------------
# Result returned to VoiceSession each turn
# ---------------------------------------------------------------------------


@dataclass
class BookingTurnResult:
    """
    Output of TourBookingCoordinator.handle_turn() for one caller turn.

    Attributes:
        sub_state:              Current sub-state after processing this turn.
        done:                   True when coordinator is finished (success or
                                non-recoverable failure). VoiceSession should
                                advance past TOUR_BOOKING.
        booking_confirmed:      True when book_tour succeeded.
        booking_id:             UUID string from BookTourResponse when confirmed.
        available_slots:        Slots fetched (up to 3) to present to caller.
        selected_slot:          The slot the caller chose (set at SLOT_SELECTED).
        escalate:               True when a failure requires human handoff.
        escalation_reason:      Why handoff is needed.
        agent_prompt:           Suggested agent utterance for this turn.
        notes:                  Internal trace notes (not spoken to caller).
    """

    sub_state: BookingSubState
    done: bool = False
    booking_confirmed: bool = False
    booking_id: str | None = None
    available_slots: list[Any] = field(default_factory=list)
    selected_slot: Any | None = None
    escalate: bool = False
    escalation_reason: str | None = None
    agent_prompt: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Date window parsing
# ---------------------------------------------------------------------------

_WEEKDAY_NAMES = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_RELATIVE_WINDOWS: dict[str, tuple[int, int]] = {
    "this week": (0, 7),
    "next week": (7, 14),
    "this weekend": (5, 7),     # crude: next Sat/Sun
    "next weekend": (12, 14),
    "tomorrow": (1, 2),
    "today": (0, 1),
}


def _parse_date_window(caller_text: str) -> tuple[date, date]:
    """
    Heuristic: extract a (start_date, end_date) window from caller text.

    Supports common phrases ("this week", "next week", "Thursday", etc.).
    Falls back to a 7-day window starting today if nothing recognized.
    """
    today = date.today()
    lower = caller_text.lower()

    for phrase, (offset_start, offset_end) in _RELATIVE_WINDOWS.items():
        if phrase in lower:
            return today + timedelta(days=offset_start), today + timedelta(days=offset_end)

    for day_name, weekday in _WEEKDAY_NAMES.items():
        if day_name in lower:
            days_ahead = (weekday - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7  # "next occurrence" if today matches
            target = today + timedelta(days=days_ahead)
            return target, target + timedelta(days=1)

    # Default fallback: next 7 days
    return today, today + timedelta(days=7)


# ---------------------------------------------------------------------------
# Confirmation parsing (re-uses same patterns as state_machine.py)
# ---------------------------------------------------------------------------

from voice_agent.conversation.state_machine import ConfirmationResult, parse_confirmation  # noqa: E402


# ---------------------------------------------------------------------------
# Slot presentation (3-slot cap)
# ---------------------------------------------------------------------------

MAX_SLOTS_PRESENTED: int = 3


def _format_slot(slot: "AvailableSlot") -> str:
    """Format a slot for verbal presentation to the caller."""
    return (
        f"{slot.date.strftime('%A, %B %d')} "
        f"at {slot.start_time.strftime('%I:%M %p').lstrip('0')}"
    )


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class TourBookingCoordinator:
    """
    Manages the TOUR_BOOKING conversation sub-state for one call.

    One instance lives per VoiceSession. It is idle until VoiceSession
    sets phase = TOUR_BOOKING; at that point every caller turn is
    delegated to handle_turn().

    The coordinator does NOT modify CallState directly — it returns a
    BookingTurnResult and VoiceSession applies the changes.

    Safety guarantee: book_tour() is called ONLY from _execute_booking(),
    which is reached ONLY after _sub_state == AWAITING_BOOKING_CONFIRMATION
    AND the caller has given a positive confirmation.
    """

    def __init__(self, client: "BackendClient") -> None:
        self._client = client
        self._sub_state: BookingSubState = BookingSubState.AWAITING_DATE_PREFERENCE
        self._presented_slots: list["AvailableSlot"] = []
        self._selected_slot: "AvailableSlot | None" = None
        self._retry_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def sub_state(self) -> BookingSubState:
        return self._sub_state

    def reset(self) -> None:
        """
        Reset back to AWAITING_DATE_PREFERENCE (e.g. caller chose to restart).
        Clears selected slot and presented slots; preserves retry count.
        """
        self._sub_state = BookingSubState.AWAITING_DATE_PREFERENCE
        self._presented_slots = []
        self._selected_slot = None

    async def handle_turn(
        self,
        caller_text: str,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> BookingTurnResult:
        """
        Process one caller turn while in TOUR_BOOKING phase.

        Dispatches to sub-state handler. Returns BookingTurnResult describing
        what happened and whether the session should exit TOUR_BOOKING.
        """
        state = self._sub_state

        if state == BookingSubState.AWAITING_DATE_PREFERENCE:
            return await self._from_awaiting_date(caller_text, property_id)

        if state == BookingSubState.AVAILABILITY_FETCHED:
            return self._from_availability_presented(caller_text)

        if state == BookingSubState.SLOT_SELECTED:
            # Move to confirmation gate.
            return self._enter_confirmation()

        if state == BookingSubState.AWAITING_BOOKING_CONFIRMATION:
            return await self._from_awaiting_confirmation(caller_text, call_id, property_id, lead_id)

        if state == BookingSubState.AWAITING_RETRY_SLOT:
            return await self._from_awaiting_date(caller_text, property_id)

        # Terminal states — coordinator is done.
        return BookingTurnResult(
            sub_state=self._sub_state,
            done=True,
            booking_confirmed=(self._sub_state == BookingSubState.BOOKING_COMPLETE),
            notes="Terminal sub-state; no further turns expected.",
        )

    # ------------------------------------------------------------------
    # Sub-state handlers
    # ------------------------------------------------------------------

    async def _from_awaiting_date(
        self, caller_text: str, property_id: uuid.UUID
    ) -> BookingTurnResult:
        """
        Caller has stated a date preference. Parse it, fetch availability.
        """
        start, end = _parse_date_window(caller_text)
        log.info(
            "booking.fetching_availability",
            extra={"start": str(start), "end": str(end)},
        )

        try:
            response = await self._client.check_tour_availability(
                property_id=property_id,
                start_date=start,
                end_date=end,
            )
        except Exception as exc:
            from voice_agent.tools.backend_client import BackendToolError
            is_retryable = exc.retryable if isinstance(exc, BackendToolError) else True
            error_code = exc.code if isinstance(exc, BackendToolError) else "UNEXPECTED_ERROR"
            # check_tour_availability is NOT retried here — we cannot offer slots we
            # couldn't fetch. Escalate immediately and let the leasing team follow up.
            log.warning(
                "booking.availability_error",
                extra={
                    "tool_name": "check_tour_availability",
                    "attempt": 1,
                    "error_code": error_code,
                    "retryable": is_retryable,
                    "error": str(exc)[:200],
                    "retry_decision": "no_retry_on_availability_failure",
                },
            )
            self._sub_state = BookingSubState.BOOKING_FAILED
            return BookingTurnResult(
                sub_state=self._sub_state,
                done=True,
                escalate=True,
                escalation_reason="booking_failed",
                agent_prompt=(
                    "I wasn't able to check availability right now. "
                    "Let me connect you with our team to find a time that works."
                ),
                notes=f"check_tour_availability failed: {exc!s}",
            )

        slots = response.available_slots[:MAX_SLOTS_PRESENTED]

        if not slots:
            # No availability — offer human follow-up; do not invent slots.
            self._sub_state = BookingSubState.BOOKING_FAILED
            log.info("booking.no_slots_available")
            return BookingTurnResult(
                sub_state=self._sub_state,
                done=True,
                escalate=True,
                escalation_reason="booking_failed",
                agent_prompt=(
                    "I don't see any open slots for that window. "
                    "Let me have our leasing team reach out to find a time that works for you."
                ),
                notes="No slots returned for requested window.",
            )

        self._presented_slots = list(slots)
        self._sub_state = BookingSubState.AVAILABILITY_FETCHED

        slot_descriptions = [
            f"Option {i+1}: {_format_slot(s)}"
            for i, s in enumerate(slots)
        ]
        options_text = "; ".join(slot_descriptions)
        return BookingTurnResult(
            sub_state=self._sub_state,
            available_slots=list(slots),
            agent_prompt=(
                f"I have a few openings: {options_text}. "
                "Which one works for you?"
            ),
            notes=f"Presented {len(slots)} slot(s).",
        )

    def _from_availability_presented(self, caller_text: str) -> BookingTurnResult:
        """
        Caller is picking a slot from the presented options.
        Heuristic: detect "option 1/2/3" or "first/second/third".
        If no slot can be identified, re-prompt.
        If caller says "none" / "different" / "different date" → re-enter AWAITING_DATE_PREFERENCE.
        """
        lower = caller_text.lower()

        # Check if caller wants different dates
        if any(kw in lower for kw in ["different", "other date", "another date", "none of", "something else"]):
            self._sub_state = BookingSubState.AWAITING_DATE_PREFERENCE
            self._presented_slots = []
            return BookingTurnResult(
                sub_state=self._sub_state,
                agent_prompt="Of course — what other dates or times work for you?",
                notes="Caller rejected presented slots; re-entering date preference.",
            )

        # Try to pick a slot by ordinal or number
        slot_index: int | None = None
        if any(kw in lower for kw in ["first", "option 1", "number 1", "1st", "one"]):
            slot_index = 0
        elif any(kw in lower for kw in ["second", "option 2", "number 2", "2nd", "two"]):
            slot_index = 1
        elif any(kw in lower for kw in ["third", "option 3", "number 3", "3rd", "three"]):
            slot_index = 2

        if slot_index is not None and slot_index < len(self._presented_slots):
            self._selected_slot = self._presented_slots[slot_index]
            self._sub_state = BookingSubState.SLOT_SELECTED
            return self._enter_confirmation()

        # Could not identify a slot — re-prompt.
        slot_descriptions = [
            f"Option {i+1}: {_format_slot(s)}"
            for i, s in enumerate(self._presented_slots)
        ]
        options_text = "; ".join(slot_descriptions)
        return BookingTurnResult(
            sub_state=self._sub_state,
            available_slots=list(self._presented_slots),
            agent_prompt=(
                f"I didn't catch that. The options are: {options_text}. "
                "Which one would you prefer?"
            ),
            notes="Could not identify slot selection; re-prompting.",
        )

    def _enter_confirmation(self) -> BookingTurnResult:
        """Move to AWAITING_BOOKING_CONFIRMATION and build confirmation prompt."""
        if self._selected_slot is None:
            # Safety guard — should not happen
            self._sub_state = BookingSubState.AWAITING_DATE_PREFERENCE
            return BookingTurnResult(
                sub_state=self._sub_state,
                agent_prompt="Let me re-check — what dates work for you?",
                notes="Selected slot was None at confirmation gate (internal error).",
            )

        self._sub_state = BookingSubState.AWAITING_BOOKING_CONFIRMATION
        slot = self._selected_slot
        formatted = _format_slot(slot)
        return BookingTurnResult(
            sub_state=self._sub_state,
            selected_slot=slot,
            agent_prompt=(
                f"Just to confirm: I'll book your tour for {formatted}. "
                "Does that sound right?"
            ),
            notes=f"Entering confirmation gate for slot: {slot.slot_id}",
        )

    async def _from_awaiting_confirmation(
        self,
        caller_text: str,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> BookingTurnResult:
        """
        Caller is responding to the confirmation prompt.
        GIVEN -> execute booking.
        REFUSED / AMBIGUOUS -> return to AWAITING_DATE_PREFERENCE.
        """
        result = parse_confirmation(caller_text)

        if result == ConfirmationResult.REFUSED or result == ConfirmationResult.AMBIGUOUS:
            self._sub_state = BookingSubState.AWAITING_DATE_PREFERENCE
            self._selected_slot = None
            self._presented_slots = []
            return BookingTurnResult(
                sub_state=self._sub_state,
                agent_prompt="No problem — what dates or times work better for you?",
                notes="Caller refused booking confirmation; returning to date preference.",
            )

        # GIVEN — execute the booking.
        return await self._execute_booking(call_id, property_id, lead_id)

    async def _execute_booking(
        self,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> BookingTurnResult:
        """
        Safety-critical: call book_tour() only after caller confirmed.
        On success: BOOKING_COMPLETE.
        On retryable failure: retry once, then BOOKING_FAILED + escalate.
        On non-retryable failure: BOOKING_FAILED + escalate immediately.
        Never claim success on any failure path.
        """
        from voice_agent.tools.backend_client import BackendToolError, BookingSlotInput

        slot = self._selected_slot
        if slot is None:
            # Guard — should never happen
            self._sub_state = BookingSubState.BOOKING_FAILED
            return BookingTurnResult(
                sub_state=self._sub_state,
                done=True,
                escalate=True,
                escalation_reason="booking_failed",
                agent_prompt=(
                    "I wasn't able to complete the booking. "
                    "Our team will follow up to confirm your tour."
                ),
                notes="book_tour called with no selected slot (internal error).",
            )

        booking_slot = BookingSlotInput(
            date=slot.date,
            start_time=slot.start_time,
            end_time=slot.end_time,
            slot_id=slot.slot_id,
        )

        for attempt in range(1, MAX_BOOKING_RETRIES + 2):
            try:
                response = await self._client.book_tour(
                    property_id=property_id,
                    lead_id=lead_id,
                    call_id=call_id,
                    selected_slot=booking_slot,
                )
                # SUCCESS
                self._sub_state = BookingSubState.BOOKING_COMPLETE
                formatted = _format_slot(slot)
                log.info(
                    "booking.confirmed",
                    extra={
                        "booking_id": str(response.booking_id),
                        "tour_date": str(response.tour_date),
                    },
                )
                return BookingTurnResult(
                    sub_state=self._sub_state,
                    done=True,
                    booking_confirmed=True,
                    booking_id=str(response.booking_id),
                    selected_slot=slot,
                    agent_prompt=(
                        f"You're all set! Your tour is confirmed for {formatted}. "
                        "We'll send a confirmation to the email on file."
                    ),
                    notes=f"Booking succeeded on attempt {attempt}.",
                )

            except BackendToolError as exc:
                if exc.code == "BOOKING_SLOT_UNAVAILABLE":
                    # Non-retryable: slot is gone — re-prompt for new slot.
                    # This does NOT count toward tool_failure_count (not a backend error;
                    # it's a normal race condition — the slot was just taken).
                    log.warning(
                        "booking.slot_unavailable",
                        extra={
                            "tool_name": "book_tour",
                            "attempt": attempt,
                            "error_code": exc.code,
                            "retryable": False,
                            "retry_decision": "re_prompt_new_slot",
                        },
                    )
                    self._sub_state = BookingSubState.AWAITING_DATE_PREFERENCE
                    self._selected_slot = None
                    self._presented_slots = []
                    return BookingTurnResult(
                        sub_state=self._sub_state,
                        agent_prompt=(
                            "Unfortunately that slot was just taken. "
                            "What other dates or times work for you?"
                        ),
                        notes=f"Slot {slot.slot_id} unavailable; re-entering date preference.",
                    )

                if not exc.retryable or attempt > MAX_BOOKING_RETRIES:
                    # Non-retryable or exhausted retries.
                    log.error(
                        "booking.failed",
                        extra={
                            "tool_name": "book_tour",
                            "attempt": attempt,
                            "error_code": exc.code,
                            "retryable": exc.retryable,
                            "retry_decision": "escalate",
                        },
                    )
                    self._sub_state = BookingSubState.BOOKING_FAILED
                    self._retry_count += 1
                    return BookingTurnResult(
                        sub_state=self._sub_state,
                        done=True,
                        escalate=True,
                        escalation_reason="booking_failed",
                        agent_prompt=(
                            "I wasn't able to complete the booking just now. "
                            "Someone from our team will reach out to confirm your tour time."
                        ),
                        notes=f"book_tour failed (code={exc.code}, attempt={attempt}).",
                    )

                # Retryable — loop and try again (max once).
                log.warning(
                    "booking.retry",
                    extra={
                        "tool_name": "book_tour",
                        "attempt": attempt,
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                        "retry_decision": "retry",
                    },
                )

        # Exhausted — should not reach here
        self._sub_state = BookingSubState.BOOKING_FAILED
        return BookingTurnResult(
            sub_state=self._sub_state,
            done=True,
            escalate=True,
            escalation_reason="booking_failed",
            agent_prompt=(
                "I wasn't able to complete the booking. "
                "Our team will follow up with you."
            ),
            notes="Exhausted all booking attempts.",
        )
