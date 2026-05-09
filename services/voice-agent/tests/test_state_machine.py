"""
Tests for LeadCaptureStateMachine.

All tests use synthetic caller text strings — no audio, no LLM, no backend calls.
Tests exercise the state machine as a pure finite-state object.

Scenarios covered:
  - Full happy-path lead-capture call (greeting -> intent -> 5-field capture
    -> confirm -> action -> close)
  - Caller jumps from lead-capture to a maintenance question and back;
    data is preserved
  - Agent attempts book_tour without confirmation -> blocked into
    AWAITING_CONFIRMATION
  - Caller says "no, that's wrong" -> re-enters LEAD_CAPTURE
  - Confirmation GIVEN -> advances to ACTION
  - Confirmation AMBIGUOUS -> treated as REFUSED (re-enters LEAD_CAPTURE)
  - Direct phase forcing (escalation, ended)
  - Intent detection: leasing, tour, maintenance, resident, escalation, unknown
  - Captured fields never cleared on topic jumps
  - Phase history tracks all transitions
"""

from __future__ import annotations

import pytest

from voice_agent.conversation.state_machine import (
    LEAD_CAPTURE_FIELDS,
    REQUIRED_FIELDS_FOR_CONFIRMATION,
    CapturedFields,
    ConversationPhase,
    ConfirmationResult,
    DetectedIntent,
    LeadCaptureStateMachine,
    detect_intent,
    parse_confirmation,
)


# ---------------------------------------------------------------------------
# Helper: build a state machine with all required fields pre-loaded
# ---------------------------------------------------------------------------

def _make_machine_with_fields(intent_text: str = "I want to rent an apartment") -> LeadCaptureStateMachine:
    """Return a machine already in LEAD_CAPTURE with all 5 required fields captured."""
    sm = LeadCaptureStateMachine()
    sm.advance(caller_text=intent_text)
    sm.advance(
        caller_text="My name is Jordan",
        extracted_fields={"name": "Jordan Lee"},
    )
    sm.advance(
        caller_text="My phone is 555-1234",
        extracted_fields={"phone": "5551234"},
    )
    sm.advance(
        caller_text="My email is jordan@example.com",
        extracted_fields={"email": "jordan@example.com"},
    )
    sm.advance(
        caller_text="I want a two bedroom",
        extracted_fields={"desired_unit_type": "2BR"},
    )
    sm.advance(
        caller_text="I want to move in on June 1st",
        extracted_fields={"move_in_date": "2026-06-01"},
    )
    return sm


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class TestDetectIntent:
    def test_leasing_inquiry(self):
        assert detect_intent("I'm interested in renting an apartment") == DetectedIntent.LEASING_INQUIRY

    def test_tour_request(self):
        assert detect_intent("I'd like to schedule a tour") == DetectedIntent.TOUR_REQUEST

    def test_maintenance(self):
        assert detect_intent("I have a maintenance issue with my heater") == DetectedIntent.MAINTENANCE

    def test_resident_support(self):
        assert detect_intent("I'm a current tenant and have a question about parking") == DetectedIntent.RESIDENT_SUPPORT

    def test_escalation_requested(self):
        assert detect_intent("I'd like to speak to a human") == DetectedIntent.ESCALATION_REQUESTED

    def test_unknown(self):
        assert detect_intent("Hello there") == DetectedIntent.UNKNOWN

    def test_tour_takes_priority_over_leasing(self):
        # "schedule" triggers tour, even though "apartment" is in the text
        assert detect_intent("I want to schedule a tour of the apartment") == DetectedIntent.TOUR_REQUEST

    def test_escalation_takes_priority_over_leasing(self):
        assert detect_intent("I want to speak to a human about renting") == DetectedIntent.ESCALATION_REQUESTED


# ---------------------------------------------------------------------------
# Confirmation parsing
# ---------------------------------------------------------------------------


class TestParseConfirmation:
    def test_yes_is_given(self):
        assert parse_confirmation("Yes that's correct") == ConfirmationResult.GIVEN

    def test_no_is_refused(self):
        assert parse_confirmation("No that's wrong") == ConfirmationResult.REFUSED

    def test_sounds_good_is_given(self):
        assert parse_confirmation("Sounds good, go ahead") == ConfirmationResult.GIVEN

    def test_cancel_is_refused(self):
        assert parse_confirmation("Actually cancel that") == ConfirmationResult.REFUSED

    def test_ambiguous_is_ambiguous(self):
        # No positive or negative markers
        assert parse_confirmation("Uh, hmm") == ConfirmationResult.AMBIGUOUS

    def test_negative_overrides_positive(self):
        # "no" in text overrides "sounds good" — negative wins
        assert parse_confirmation("No actually that doesn't sound good") == ConfirmationResult.REFUSED


# ---------------------------------------------------------------------------
# CapturedFields
# ---------------------------------------------------------------------------


class TestCapturedFields:
    def test_set_and_get(self):
        cf = CapturedFields()
        cf.set("name", "Jordan")
        assert cf.get("name") == "Jordan"

    def test_none_value_not_stored(self):
        cf = CapturedFields()
        cf.set("name", None)
        assert cf.get("name") is None
        assert "name" not in cf.data

    def test_missing_all_when_empty(self):
        cf = CapturedFields()
        missing = cf.missing()
        assert set(missing) == set(LEAD_CAPTURE_FIELDS)

    def test_missing_excludes_captured(self):
        cf = CapturedFields()
        cf.set("name", "Jordan")
        cf.set("phone", "555-1234")
        missing = cf.missing()
        assert "name" not in missing
        assert "phone" not in missing
        assert "email" in missing

    def test_all_required_present_false_when_incomplete(self):
        cf = CapturedFields()
        cf.set("name", "Jordan")
        assert cf.all_required_present() is False

    def test_all_required_present_true_when_complete(self):
        cf = CapturedFields()
        for f in REQUIRED_FIELDS_FOR_CONFIRMATION:
            cf.set(f, "value")
        assert cf.all_required_present() is True

    def test_as_dict_returns_copy(self):
        cf = CapturedFields()
        cf.set("name", "Jordan")
        d = cf.as_dict()
        d["name"] = "mutated"
        assert cf.get("name") == "Jordan"  # original unchanged


# ---------------------------------------------------------------------------
# Full happy-path: greeting -> intent -> 5-field capture -> confirm -> action -> close
# ---------------------------------------------------------------------------


class TestHappyPathLeadCapture:
    def test_greeting_phase_initial(self):
        sm = LeadCaptureStateMachine()
        assert sm.phase == ConversationPhase.GREETING

    def test_first_turn_detects_leasing_intent(self):
        sm = LeadCaptureStateMachine()
        result = sm.advance("I'm interested in renting an apartment")
        assert result.intent == DetectedIntent.LEASING_INQUIRY
        assert result.new_phase == ConversationPhase.LEAD_CAPTURE

    def test_lead_capture_asks_for_first_missing_field(self):
        sm = LeadCaptureStateMachine()
        result = sm.advance("I want to rent an apartment")
        assert result.next_field_to_ask == "name"
        assert "name" in result.missing_fields

    def test_field_captured_reduces_missing(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        result = sm.advance("My name is Jordan", extracted_fields={"name": "Jordan"})
        assert "name" not in result.missing_fields
        assert result.next_field_to_ask == "phone"

    def test_all_five_fields_captured_sequentially(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        sm.advance("Jordan Lee", extracted_fields={"name": "Jordan Lee"})
        sm.advance("555-1234", extracted_fields={"phone": "5551234"})
        sm.advance("jordan@example.com", extracted_fields={"email": "jordan@example.com"})
        sm.advance("two bedroom", extracted_fields={"desired_unit_type": "2BR"})
        result = sm.advance("June 1st", extracted_fields={"move_in_date": "2026-06-01"})

        # All five fields should now be captured.
        missing = sm.captured_fields.missing()
        assert missing == []
        assert sm.captured_fields.get("name") == "Jordan Lee"
        assert sm.captured_fields.get("email") == "jordan@example.com"

    def test_confirmation_gate_entered_when_all_fields_and_action_ready(self):
        sm = _make_machine_with_fields()
        # Trigger action_ready=True — machine should gate into confirmation.
        result = sm.advance("I'd like to book a tour", action_ready=True)
        assert result.new_phase == ConversationPhase.AWAITING_CONFIRMATION
        assert result.confirmation_needed is True
        assert result.confirmation_summary is not None
        assert "Jordan" in result.confirmation_summary  # name in summary

    def test_confirmation_given_advances_to_action(self):
        sm = _make_machine_with_fields()
        sm.advance("I'd like to book a tour", action_ready=True)
        # Now in AWAITING_CONFIRMATION — resolve with positive response.
        result = sm.resolve_confirmation("Yes that's correct")
        assert result.new_phase == ConversationPhase.ACTION
        assert result.confirmation_result == ConfirmationResult.GIVEN

    def test_action_phase_advances_to_closing(self):
        sm = _make_machine_with_fields()
        sm.advance("I'd like to book a tour", action_ready=True)
        sm.resolve_confirmation("Yes that's correct")
        assert sm.phase == ConversationPhase.ACTION
        # One more turn from ACTION moves to CLOSING.
        result = sm.advance("Great, thank you")
        assert result.new_phase == ConversationPhase.CLOSING

    def test_closing_phase_advances_to_ended(self):
        sm = _make_machine_with_fields()
        sm.advance("I'd like to book a tour", action_ready=True)
        sm.resolve_confirmation("Yes")
        sm.advance("Thanks")  # ACTION -> CLOSING
        result = sm.advance("Goodbye")   # CLOSING -> ENDED
        assert result.new_phase == ConversationPhase.ENDED

    def test_phase_history_records_all_transitions(self):
        sm = _make_machine_with_fields()
        sm.advance("Book it", action_ready=True)
        sm.resolve_confirmation("Yes")
        sm.advance("ok")     # ACTION -> CLOSING
        sm.advance("bye")    # CLOSING -> ENDED
        history = sm.phase_history
        assert ConversationPhase.GREETING in history
        assert ConversationPhase.LEAD_CAPTURE in history
        assert ConversationPhase.AWAITING_CONFIRMATION in history
        assert ConversationPhase.ACTION in history
        assert ConversationPhase.CLOSING in history
        assert ConversationPhase.ENDED in history


# ---------------------------------------------------------------------------
# Topic jump: lead-capture -> maintenance -> back to leasing; data preserved
# ---------------------------------------------------------------------------


class TestTopicJumpPreservesData:
    def test_fields_preserved_when_jumping_to_resident_support(self):
        """
        Caller provides name/phone then mentions a maintenance issue.
        State machine moves to RESIDENT_SUPPORT but captured name/phone survive.
        """
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        sm.advance("Jordan Lee", extracted_fields={"name": "Jordan Lee"})
        sm.advance("555-1234", extracted_fields={"phone": "5551234"})

        # Topic jump to maintenance
        result = sm.advance("Actually I have a maintenance issue with my heater")
        assert result.new_phase == ConversationPhase.RESIDENT_SUPPORT

        # Lead fields must still be there
        assert sm.captured_fields.get("name") == "Jordan Lee"
        assert sm.captured_fields.get("phone") == "5551234"

    def test_returning_to_leasing_after_resident_support_resumes_capture(self):
        """
        After jumping to resident support, caller asks about an apartment again.
        Machine should return to LEAD_CAPTURE with preserved fields.
        """
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        sm.advance("Jordan", extracted_fields={"name": "Jordan"})
        sm.advance("My heater is broken")  # jump to resident support

        # Return to leasing — use clear leasing keywords
        result = sm.advance("Actually can we go back to apartment availability?")
        assert result.new_phase == ConversationPhase.LEAD_CAPTURE
        # Name still captured
        assert sm.captured_fields.get("name") == "Jordan"
        # Missing fields list still reflects what's not yet captured
        assert "name" not in result.missing_fields

    def test_missing_fields_after_topic_jump_reflect_only_uncaptured(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        sm.advance("Jordan", extracted_fields={"name": "Jordan"})
        sm.advance("phone is 555", extracted_fields={"phone": "555"})
        sm.advance("I have a pest problem")  # resident support jump
        result = sm.advance("Can I ask about apartment availability again?")
        # name and phone captured; email/unit/move_in_date not captured yet
        # The machine should now be in LEAD_CAPTURE with those fields intact
        assert "name" not in result.missing_fields
        assert "phone" not in result.missing_fields
        assert "email" in result.missing_fields


# ---------------------------------------------------------------------------
# Confirmation gate: blocked, refused, ambiguous
# ---------------------------------------------------------------------------


class TestConfirmationGate:
    def test_action_ready_without_all_fields_does_not_enter_confirmation(self):
        """
        action_ready=True with incomplete fields must NOT jump to confirmation.
        Machine stays in LEAD_CAPTURE and keeps asking for missing fields.
        """
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        # Only provide name — other fields missing
        sm.advance("Jordan", extracted_fields={"name": "Jordan"})
        # Signal action_ready before fields are complete
        result = sm.advance("I'm ready to book", action_ready=True)
        # Must stay in LEAD_CAPTURE, not jump to confirmation
        assert result.new_phase == ConversationPhase.LEAD_CAPTURE
        assert result.confirmation_needed is False

    def test_book_tour_blocked_without_confirmation(self):
        """
        Machine cannot reach ACTION without going through AWAITING_CONFIRMATION.
        If action_ready fires without confirmation, machine gates.
        """
        sm = _make_machine_with_fields()
        result = sm.advance("I want to book", action_ready=True)
        assert result.new_phase == ConversationPhase.AWAITING_CONFIRMATION
        assert result.action_blocked is False  # not blocked — gated (different thing)
        assert result.confirmation_needed is True

    def test_confirmation_refused_re_enters_lead_capture(self):
        sm = _make_machine_with_fields()
        sm.advance("Book it", action_ready=True)
        result = sm.resolve_confirmation("No that's wrong")
        assert result.new_phase == ConversationPhase.LEAD_CAPTURE
        assert result.confirmation_result == ConfirmationResult.REFUSED

    def test_confirmation_ambiguous_treated_as_refused(self):
        sm = _make_machine_with_fields()
        sm.advance("Book it", action_ready=True)
        result = sm.resolve_confirmation("Uh, I dunno")
        assert result.new_phase == ConversationPhase.LEAD_CAPTURE
        assert result.confirmation_result == ConfirmationResult.AMBIGUOUS

    def test_fields_preserved_after_refused_confirmation(self):
        """After refusal, previously captured data must survive."""
        sm = _make_machine_with_fields()
        sm.advance("Book it", action_ready=True)
        sm.resolve_confirmation("No, the email is wrong")
        # All originally captured fields should still be in place
        assert sm.captured_fields.get("name") == "Jordan Lee"
        assert sm.captured_fields.get("phone") == "5551234"

    def test_confirmation_summary_includes_name_and_email(self):
        sm = _make_machine_with_fields()
        result = sm.advance("Book tour", action_ready=True)
        assert result.confirmation_summary is not None
        assert "Jordan" in result.confirmation_summary
        assert "jordan@example.com" in result.confirmation_summary

    def test_phone_redacted_in_confirmation_summary(self):
        """Phone last 4 digits shown only — middle digits redacted."""
        sm = _make_machine_with_fields()
        result = sm.advance("Book tour", action_ready=True)
        summary = result.confirmation_summary or ""
        # Full phone "5551234" should not appear verbatim; only last 4
        assert "5551234" not in summary
        assert "1234" in summary


# ---------------------------------------------------------------------------
# Escalation path from state machine
# ---------------------------------------------------------------------------


class TestStateMachineEscalation:
    def test_explicit_escalation_request_from_greeting(self):
        sm = LeadCaptureStateMachine()
        result = sm.advance("I want to speak to a human please")
        assert result.new_phase == ConversationPhase.ESCALATION
        assert result.intent == DetectedIntent.ESCALATION_REQUESTED

    def test_explicit_escalation_mid_lead_capture(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        sm.advance("Jordan", extracted_fields={"name": "Jordan"})
        result = sm.advance("Actually get me a supervisor")
        assert result.new_phase == ConversationPhase.ESCALATION

    def test_force_phase_escalation(self):
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.ESCALATION)
        assert sm.phase == ConversationPhase.ESCALATION

    def test_terminal_phases_stay_put(self):
        sm = LeadCaptureStateMachine()
        sm.force_phase(ConversationPhase.ESCALATION)
        result = sm.advance("Hello again")
        # ESCALATION is terminal — stays in ESCALATION
        assert result.new_phase == ConversationPhase.ESCALATION

    def test_primary_intent_for_summary_leasing(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        assert sm.primary_intent_for_summary() == "leasing_inquiry"

    def test_primary_intent_for_summary_tour(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to schedule a tour")
        assert sm.primary_intent_for_summary() == "tour_request"

    def test_primary_intent_for_summary_unknown_default(self):
        sm = LeadCaptureStateMachine()
        assert sm.primary_intent_for_summary() == "unknown"
