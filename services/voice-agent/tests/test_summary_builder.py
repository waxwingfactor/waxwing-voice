"""
Tests for SummaryBuilder.

Uses CallState and LeadCaptureStateMachine instances directly — no backend,
no audio, no LLM calls.

Scenarios:
  - End-of-call snapshot produces correct SaveCallSummaryRequest shape
  - lead_fields_extracted matches state machine's final captured fields
  - Escalated call produces escalation_flag=True and appropriate next_steps
  - Booking confirmed produces booking-specific next_steps
  - ai_summary used when present; fallback used when absent
  - Sentiment forwarded correctly from CallState
  - Action items derived from incomplete lead capture
  - State machine's primary_intent_for_summary() used when provided
  - SummaryBuilder without state machine falls back to CallState.intent
"""

from __future__ import annotations

from datetime import date, time

import pytest

from voice_agent.conversation.state_machine import LeadCaptureStateMachine
from voice_agent.conversation.summary_builder import SummaryBuilder
from voice_agent.state.call_state import (
    CallPhase,
    CallState,
    CallerIntent,
    EscalationReason,
    LeadFields,
    TourSlot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_state(**kwargs) -> CallState:
    return CallState(property_id="prop-test-001", **kwargs)


def _make_ended_state(**kwargs) -> CallState:
    state = _make_state(**kwargs)
    state.mark_ended()
    return state


def _make_machine_with_leasing_intent() -> LeadCaptureStateMachine:
    sm = LeadCaptureStateMachine()
    sm.advance("I want to rent an apartment")
    return sm


def _make_machine_with_all_fields() -> LeadCaptureStateMachine:
    sm = LeadCaptureStateMachine()
    sm.advance("I want to rent an apartment")
    sm.advance("Jordan", extracted_fields={"name": "Jordan Lee"})
    sm.advance("555-1234", extracted_fields={"phone": "5551234"})
    sm.advance("jordan@example.com", extracted_fields={"email": "jordan@example.com"})
    sm.advance("2BR", extracted_fields={"desired_unit_type": "2BR"})
    sm.advance("June 1st", extracted_fields={"move_in_date": "2026-06-01"})
    return sm


# ---------------------------------------------------------------------------
# Schema shape tests
# ---------------------------------------------------------------------------


class TestSummaryBuilderSchema:
    def test_required_keys_present(self):
        """All SaveCallSummaryRequest fields must be present."""
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)

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

    def test_no_extra_backend_forbidden_keys(self):
        """Keys NOT in Harsha's schema must not appear."""
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)

        forbidden_keys = {
            "property_id",
            "escalation_reason",
            "duration_seconds",
            "booking_id",
            "follow_up_email_sent",
            "confidence_score_final",
            "tool_failure_count",
        }
        for key in forbidden_keys:
            assert key not in payload, f"Forbidden key present: {key}"

    def test_call_id_matches_state(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["call_id"] == state.call_id

    def test_escalation_flag_false_by_default(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["escalation_flag"] is False

    def test_escalation_flag_true_when_triggered(self):
        state = _make_state()
        state.trigger_escalation(EscalationReason.LEGAL_QUESTION, "Legal escalation")
        state.mark_ended()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["escalation_flag"] is True

    def test_action_items_is_list(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert isinstance(payload["action_items"], list)

    def test_sentiment_forwarded(self):
        state = _make_ended_state()
        state.sentiment = "frustrated"
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["sentiment"] == "frustrated"

    def test_lead_fields_extracted_is_dict(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert isinstance(payload["lead_fields_extracted"], dict)


# ---------------------------------------------------------------------------
# lead_fields_extracted accuracy
# ---------------------------------------------------------------------------


class TestLeadFieldsExtracted:
    def test_empty_lead_fields_gives_empty_dict(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["lead_fields_extracted"] == {}

    def test_captured_fields_appear_in_payload(self):
        state = _make_ended_state()
        state.lead_fields.name = "Jordan Lee"
        state.lead_fields.email = "jordan@example.com"
        builder = SummaryBuilder()
        payload = builder.build(state)
        extracted = payload["lead_fields_extracted"]
        assert extracted["name"] == "Jordan Lee"
        assert extracted["email"] == "jordan@example.com"

    def test_email_confirmed_excluded_from_payload(self):
        """email_confirmed is local-only; must not appear in backend payload."""
        state = _make_ended_state()
        state.lead_fields.email = "jordan@example.com"
        state.lead_fields.email_confirmed = True
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert "email_confirmed" not in payload["lead_fields_extracted"]

    def test_none_fields_excluded_from_payload(self):
        """Only non-None lead fields should appear."""
        state = _make_ended_state()
        state.lead_fields.name = "Jordan"
        # email, phone, etc. remain None
        builder = SummaryBuilder()
        payload = builder.build(state)
        extracted = payload["lead_fields_extracted"]
        assert "name" in extracted
        assert "email" not in extracted
        assert "phone" not in extracted

    def test_lead_fields_match_state_machine_captured_fields(self):
        """
        When VoiceSession updates CallState.lead_fields from state machine,
        the summary payload should reflect those exact fields.

        This test simulates the VoiceSession updating lead_fields from
        state machine captured data (as would happen in production).
        """
        sm = _make_machine_with_all_fields()
        state = _make_state()

        # Simulate VoiceSession copying state machine fields to CallState.lead_fields
        captured = sm.captured_fields.as_dict()
        state.lead_fields.name = captured.get("name")
        state.lead_fields.phone = captured.get("phone")
        state.lead_fields.email = captured.get("email")
        state.lead_fields.desired_unit_type = captured.get("desired_unit_type")
        state.mark_ended()

        builder = SummaryBuilder()
        payload = builder.build(state, sm)
        extracted = payload["lead_fields_extracted"]

        assert extracted["name"] == "Jordan Lee"
        assert extracted["phone"] == "5551234"
        assert extracted["email"] == "jordan@example.com"
        assert extracted["desired_unit_type"] == "2BR"


# ---------------------------------------------------------------------------
# primary_intent
# ---------------------------------------------------------------------------


class TestPrimaryIntent:
    def test_uses_state_machine_intent_when_provided(self):
        state = _make_ended_state()
        sm = _make_machine_with_leasing_intent()
        builder = SummaryBuilder()
        payload = builder.build(state, sm)
        assert payload["primary_intent"] == "leasing_inquiry"

    def test_falls_back_to_call_state_intent_when_no_machine(self):
        state = _make_ended_state()
        state.intent = CallerIntent.MAINTENANCE
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["primary_intent"] == "maintenance"

    def test_unknown_intent_when_no_turns_made(self):
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state)
        # No state machine, no intent set on state -> "unknown"
        assert payload["primary_intent"] == "unknown"

    def test_tour_request_intent(self):
        sm = LeadCaptureStateMachine()
        sm.advance("I want to schedule a tour")
        state = _make_ended_state()
        builder = SummaryBuilder()
        payload = builder.build(state, sm)
        assert payload["primary_intent"] == "tour_request"


# ---------------------------------------------------------------------------
# summary text
# ---------------------------------------------------------------------------


class TestSummaryText:
    def test_ai_summary_used_when_present(self):
        state = _make_ended_state()
        state.ai_summary = "The caller asked about one-bedroom apartments and scheduled a tour."
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["summary"] == state.ai_summary

    def test_fallback_summary_generated_when_ai_summary_absent(self):
        state = _make_ended_state()
        assert state.ai_summary is None
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert isinstance(payload["summary"], str)
        assert len(payload["summary"]) > 10  # non-trivial

    def test_fallback_summary_includes_phase(self):
        state = _make_state()
        state.phase = CallPhase.CLOSING
        state.mark_ended()
        builder = SummaryBuilder()
        payload = builder.build(state)
        # Phase name should appear in fallback summary
        assert "ended" in payload["summary"].lower() or "closing" in payload["summary"].lower()

    def test_fallback_summary_includes_escalation_info_when_escalated(self):
        state = _make_state()
        state.trigger_escalation(EscalationReason.FAIR_HOUSING_QUESTION)
        state.mark_ended()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert "escalated" in payload["summary"].lower() or "yes" in payload["summary"].lower()


# ---------------------------------------------------------------------------
# next_steps derivation
# ---------------------------------------------------------------------------


class TestNextSteps:
    def test_booking_confirmed_produces_booking_next_steps(self):
        state = _make_ended_state()
        state.booking_confirmed = True
        state.selected_tour_slot = TourSlot(
            slot_id="slot-01",
            date=date(2026, 6, 15),
            start_time=time(10, 0),
            end_time=time(10, 30),
        )
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["next_steps"] is not None
        assert "tour" in payload["next_steps"].lower() or "booked" in payload["next_steps"].lower()

    def test_escalated_call_produces_escalation_next_steps(self):
        state = _make_state()
        state.trigger_escalation(EscalationReason.EMERGENCY)
        state.mark_ended()
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["next_steps"] is not None
        assert "emergency" in payload["next_steps"].lower() or "handoff" in payload["next_steps"].lower()

    def test_incomplete_lead_produces_follow_up_next_steps(self):
        state = _make_state()
        state.mark_ended()
        sm = LeadCaptureStateMachine()
        sm.advance("I want to rent an apartment")
        # Only name captured — rest missing
        sm.advance("Jordan", extracted_fields={"name": "Jordan"})

        builder = SummaryBuilder()
        payload = builder.build(state, sm)
        assert payload["next_steps"] is not None
        assert "missing" in payload["next_steps"].lower() or "incomplete" in payload["next_steps"].lower()

    def test_ai_next_steps_used_when_present(self):
        state = _make_ended_state()
        state.ai_next_steps = "Send follow-up email and confirm tour."
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["next_steps"] == state.ai_next_steps


# ---------------------------------------------------------------------------
# action_items derivation
# ---------------------------------------------------------------------------


class TestActionItems:
    def test_ai_action_items_used_when_present(self):
        state = _make_ended_state()
        state.ai_action_items = ["Send confirmation email", "Update CRM"]
        builder = SummaryBuilder()
        payload = builder.build(state)
        assert payload["action_items"] == ["Send confirmation email", "Update CRM"]

    def test_missing_contact_fields_generate_follow_up_item(self):
        state = _make_ended_state()
        # No lead fields populated
        builder = SummaryBuilder()
        payload = builder.build(state)
        items = payload["action_items"]
        # At least one item about following up
        combined = " ".join(items).lower()
        assert "follow" in combined or "missing" in combined or "capture" in combined

    def test_legal_escalation_generates_compliance_item(self):
        state = _make_state()
        state.trigger_escalation(EscalationReason.LEGAL_QUESTION)
        state.mark_ended()
        builder = SummaryBuilder()
        payload = builder.build(state)
        combined = " ".join(payload["action_items"]).lower()
        assert "legal" in combined or "compliance" in combined or "review" in combined

    def test_follow_up_email_item_when_email_confirmed_but_not_sent(self):
        state = _make_ended_state()
        state.lead_fields.email = "jordan@example.com"
        state.lead_fields.email_confirmed = True
        state.follow_up_email_sent = False
        builder = SummaryBuilder()
        payload = builder.build(state)
        combined = " ".join(payload["action_items"]).lower()
        assert "email" in combined

    def test_no_duplicate_email_item_when_already_sent(self):
        state = _make_ended_state()
        state.lead_fields.email = "jordan@example.com"
        state.lead_fields.email_confirmed = True
        state.follow_up_email_sent = True
        builder = SummaryBuilder()
        payload = builder.build(state)
        # Should not generate "send email" item since already sent
        combined = " ".join(payload["action_items"]).lower()
        # "send follow-up email" specifically should not appear
        assert "send follow-up" not in combined


# ---------------------------------------------------------------------------
# Integration: full call snapshot
# ---------------------------------------------------------------------------


class TestFullCallSnapshot:
    def test_happy_path_snapshot_shape(self):
        """
        Full happy-path: prospect called, captured all fields, tour booked.
        Summary must hit the right schema shape.
        """
        sm = _make_machine_with_all_fields()
        state = _make_state()

        # Populate lead fields from state machine
        captured = sm.captured_fields.as_dict()
        state.lead_fields.name = captured.get("name")
        state.lead_fields.phone = captured.get("phone")
        state.lead_fields.email = captured.get("email")
        state.lead_fields.desired_unit_type = captured.get("desired_unit_type")
        state.lead_fields.email_confirmed = True

        # Booking confirmed
        state.booking_confirmed = True
        state.selected_tour_slot = TourSlot(
            slot_id="slot-01",
            date=date(2026, 6, 15),
            start_time=time(10, 0),
            end_time=time(10, 30),
        )
        state.follow_up_email_sent = True
        state.sentiment = "positive"
        state.intent = CallerIntent.LEASING_INQUIRY
        state.mark_ended()

        builder = SummaryBuilder()
        payload = builder.build(state, sm)

        # Shape checks
        assert payload["call_id"] == state.call_id
        assert payload["escalation_flag"] is False
        assert payload["sentiment"] == "positive"
        # State machine's first turn was "I want to rent an apartment" -> leasing_inquiry
        assert payload["primary_intent"] == "leasing_inquiry"
        assert payload["lead_fields_extracted"]["name"] == "Jordan Lee"
        assert "email_confirmed" not in payload["lead_fields_extracted"]
        assert payload["next_steps"] is not None
        # Booking confirmed -> booking-focused next steps
        next_steps_lower = (payload["next_steps"] or "").lower()
        assert "tour" in next_steps_lower or "booked" in next_steps_lower
