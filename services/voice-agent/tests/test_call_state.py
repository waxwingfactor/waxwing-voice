"""
Unit tests for call state model.

These tests cover the core Pydantic models. No backend connections needed.
Run with: pytest tests/test_call_state.py
"""

import pytest

from voice_agent.state.call_state import (
    ESCALATION_CONFIDENCE_THRESHOLD,
    CallPhase,
    CallState,
    CallerIntent,
    EscalationReason,
    LeadFields,
    SpeakerRole,
    TranscriptSegment,
    TourSlot,
)


# ---------------------------------------------------------------------------
# TranscriptSegment
# ---------------------------------------------------------------------------


class TestTranscriptSegment:
    def test_basic_creation(self):
        seg = TranscriptSegment(speaker=SpeakerRole.CALLER, text="Hi there")
        assert seg.speaker == SpeakerRole.CALLER
        assert seg.text == "Hi there"
        assert seg.flushed_to_backend is False
        assert seg.segment_id  # UUID assigned

    def test_text_is_stripped(self):
        seg = TranscriptSegment(speaker=SpeakerRole.AGENT, text="  Hello.  ")
        assert seg.text == "Hello."

    def test_empty_text_rejected(self):
        with pytest.raises(Exception):
            TranscriptSegment(speaker=SpeakerRole.CALLER, text="")


# ---------------------------------------------------------------------------
# LeadFields
# ---------------------------------------------------------------------------


class TestLeadFields:
    def test_all_optional_empty(self):
        lf = LeadFields()
        assert lf.name is None
        assert lf.email is None
        assert lf.email_confirmed is False

    def test_partial_population(self):
        lf = LeadFields(name="Jordan Lee", tour_interest=True)
        assert lf.name == "Jordan Lee"
        assert lf.tour_interest is True
        assert lf.email is None

    def test_model_dump_exclude_none(self):
        lf = LeadFields(name="Jordan", email="j@test.com")
        dumped = lf.model_dump(exclude_none=True)
        assert "name" in dumped
        assert "email" in dumped
        assert "budget_min" not in dumped


# ---------------------------------------------------------------------------
# CallState
# ---------------------------------------------------------------------------


class TestCallState:
    def _make_state(self, **kwargs) -> CallState:
        return CallState(property_id="prop_test", **kwargs)

    def test_initial_state(self):
        state = self._make_state()
        assert state.phase == CallPhase.GREETING
        assert state.intent == CallerIntent.UNKNOWN
        assert state.escalation_flag is False
        assert state.escalation_reason is None
        assert state.confidence_score == 1.0
        assert len(state.transcript) == 0
        assert state.call_id  # UUID assigned
        assert state.property_id == "prop_test"

    def test_add_segment(self):
        state = self._make_state()
        seg = state.add_segment(SpeakerRole.CALLER, "I want to ask about rent.")
        assert len(state.transcript) == 1
        assert seg.speaker == SpeakerRole.CALLER
        assert seg.flushed_to_backend is False

    def test_unflushed_segments(self):
        state = self._make_state()
        s1 = state.add_segment(SpeakerRole.CALLER, "Hello")
        s2 = state.add_segment(SpeakerRole.AGENT, "Hi there!")
        assert len(state.unflushed_segments) == 2
        s1.flushed_to_backend = True
        assert len(state.unflushed_segments) == 1
        assert state.unflushed_segments[0] == s2

    def test_set_phase(self):
        state = self._make_state()
        state.set_phase(CallPhase.INTENT_DETECTION)
        assert state.phase == CallPhase.INTENT_DETECTION

    def test_trigger_escalation(self):
        state = self._make_state()
        state.trigger_escalation(
            EscalationReason.FAIR_HOUSING_QUESTION,
            notes="Caller asked about family size restrictions.",
        )
        assert state.escalation_flag is True
        assert state.escalation_reason == EscalationReason.FAIR_HOUSING_QUESTION
        assert state.phase == CallPhase.ESCALATION
        assert "family size" in state.escalation_notes

    def test_mark_ended(self):
        state = self._make_state()
        assert state.ended_at is None
        state.mark_ended()
        assert state.ended_at is not None
        assert state.phase == CallPhase.ENDED

    def test_duration_seconds_none_before_end(self):
        state = self._make_state()
        assert state.duration_seconds is None

    def test_duration_seconds_after_end(self):
        state = self._make_state()
        state.mark_ended()
        assert state.duration_seconds is not None
        assert state.duration_seconds >= 0.0

    def test_should_escalate_on_low_confidence(self):
        state = self._make_state()
        state.confidence_score = ESCALATION_CONFIDENCE_THRESHOLD - 0.01
        assert state.should_escalate_on_confidence is True

    def test_should_not_escalate_on_sufficient_confidence(self):
        state = self._make_state()
        state.confidence_score = ESCALATION_CONFIDENCE_THRESHOLD + 0.01
        assert state.should_escalate_on_confidence is False

    def test_confidence_clamped(self):
        with pytest.raises(Exception):
            self._make_state(confidence_score=1.5)
        with pytest.raises(Exception):
            self._make_state(confidence_score=-0.1)

    def test_summary_payload_shape(self):
        """
        summary_payload() must match SaveCallSummaryRequest from
        services/api/app/schemas/voice_tools.py exactly.

        Fields in Harsha's schema: call_id, summary, primary_intent, sentiment,
        action_items, escalation_flag, lead_fields_extracted, next_steps.

        Fields NOT in Harsha's schema (verified against voice_tools.py):
          property_id, escalation_reason, duration_seconds, booking_confirmed,
          booking_id, follow_up_email_sent, confidence_score_final,
          tool_failure_count.
        """
        state = self._make_state()
        state.intent = CallerIntent.LEASING_INQUIRY
        state.mark_ended()
        payload = state.summary_payload()

        # Fields Harsha's schema REQUIRES
        assert payload["call_id"] == state.call_id
        assert payload["primary_intent"] == "leasing_inquiry"
        assert payload["escalation_flag"] is False
        assert "summary" in payload
        assert "sentiment" in payload
        assert "action_items" in payload
        assert "lead_fields_extracted" in payload
        # next_steps is optional in Harsha's schema — key may be absent or None
        assert "next_steps" not in payload or payload["next_steps"] is None

        # Fields that are NOT in Harsha's schema — must NOT be present
        assert "property_id" not in payload
        assert "escalation_reason" not in payload
        assert "duration_seconds" not in payload

    def test_summary_payload_with_escalation(self):
        """
        escalation_flag is a bool in Harsha's schema.
        escalation_reason is local-only (not sent to backend).
        """
        state = self._make_state()
        state.trigger_escalation(EscalationReason.LEGAL_QUESTION)
        state.mark_ended()
        payload = state.summary_payload()
        assert payload["escalation_flag"] is True
        # escalation_reason is intentionally absent from Harsha's summary schema
        assert "escalation_reason" not in payload

    def test_tour_slot_stored(self):
        """
        TourSlot maps to BookingSlotInput in Harsha's schema.
        Fields: slot_id, date, start_time, end_time (datetime.time objects).
        Removed from old draft: time (str), timezone, duration_minutes.
        """
        from datetime import date, time

        state = self._make_state()
        slot = TourSlot(
            slot_id="slot_001",
            date=date(2025, 9, 15),
            start_time=time(10, 0),
            end_time=time(10, 30),
        )
        state.available_tour_slots = [slot]
        state.selected_tour_slot = slot
        assert state.selected_tour_slot.slot_id == "slot_001"
        assert state.selected_tour_slot.start_time == time(10, 0)
        assert state.selected_tour_slot.end_time == time(10, 30)

    def test_lead_fields_in_summary_exclude_none(self):
        """
        lead_fields_extracted in Harsha's schema is a dict snapshot (not
        LeadFieldsInput). The key is 'lead_fields_extracted', not 'lead_fields'.
        Only non-None LeadFields values are included.
        """
        state = self._make_state()
        state.lead_fields.name = "Jordan"
        state.mark_ended()
        payload = state.summary_payload()
        assert payload["lead_fields_extracted"]["name"] == "Jordan"
        # Old draft field names must NOT be present
        assert "budget_min" not in payload["lead_fields_extracted"]
        assert "phone_number" not in payload["lead_fields_extracted"]
