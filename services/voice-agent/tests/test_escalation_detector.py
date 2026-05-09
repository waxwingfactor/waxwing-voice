"""
Tests for EscalationDetector.

No audio, no backend, no LLM — pure text input to keyword scanner.

Scenarios:
  - Fair Housing keyword in caller turn -> flag set, urgency high
  - Emergency keyword -> flag set, urgency emergency
  - Legal question keyword -> flag set, urgency high
  - Financial advice keyword -> flag set, urgency high
  - Emotional distress markers -> signal returned
  - 3 consecutive tool failures -> flag set, urgency medium
  - 2 tool failures (below threshold) -> no signal
  - Safe benign text -> no signal
  - Custom threshold override
"""

from __future__ import annotations

import pytest

from voice_agent.conversation.escalation import (
    DEFAULT_TOOL_FAILURE_THRESHOLD,
    EMERGENCY_KEYWORDS,
    FAIR_HOUSING_KEYWORDS,
    FINANCIAL_KEYWORDS,
    LEGAL_KEYWORDS,
    EscalationDetector,
    EscalationSignal,
)
from voice_agent.state.call_state import EscalationReason, HandoffUrgency


class TestEscalationDetectorFairHousing:
    def test_race_keyword_triggers_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("Can you tell me what race of people live there?")
        assert signal is not None
        assert signal.reason == EscalationReason.FAIR_HOUSING_QUESTION
        assert signal.urgency == HandoffUrgency.HIGH.value

    def test_disability_keyword_triggers_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("Are there accommodations for someone with a disability?")
        assert signal is not None
        assert signal.reason == EscalationReason.FAIR_HOUSING_QUESTION

    def test_children_no_kids_triggers_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("Are there no kids allowed?")
        assert signal is not None
        assert signal.reason == EscalationReason.FAIR_HOUSING_QUESTION

    def test_religion_triggers_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("I go to mosque, will that be a problem?")
        assert signal is not None
        assert signal.reason == EscalationReason.FAIR_HOUSING_QUESTION

    def test_nationality_triggers_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("Does it matter what country of origin you're from?")
        assert signal is not None
        assert signal.reason == EscalationReason.FAIR_HOUSING_QUESTION

    def test_urgency_is_high_for_fair_housing(self):
        detector = EscalationDetector()
        signal = detector.check("What race do you rent to?")
        assert signal.urgency == "high"

    def test_signal_notes_contain_detected_keyword(self):
        detector = EscalationDetector()
        signal = detector.check("Can you tell me about the disability accommodations?")
        assert signal is not None
        assert "disability" in signal.notes.lower() or "keyword" in signal.notes.lower()


class TestEscalationDetectorEmergency:
    def test_fire_triggers_emergency(self):
        detector = EscalationDetector()
        signal = detector.check("There's a fire in my building!")
        assert signal is not None
        assert signal.reason == EscalationReason.EMERGENCY
        assert signal.urgency == HandoffUrgency.EMERGENCY.value

    def test_gas_leak_triggers_emergency(self):
        detector = EscalationDetector()
        signal = detector.check("I smell a gas leak in my unit")
        assert signal is not None
        assert signal.reason == EscalationReason.EMERGENCY

    def test_medical_emergency_triggers_emergency(self):
        detector = EscalationDetector()
        signal = detector.check("We have a medical emergency, please call 911")
        assert signal is not None
        assert signal.reason == EscalationReason.EMERGENCY
        assert signal.urgency == "emergency"

    def test_break_in_triggers_emergency(self):
        detector = EscalationDetector()
        signal = detector.check("Someone is breaking in to my apartment")
        assert signal is not None
        assert signal.reason == EscalationReason.EMERGENCY

    def test_carbon_monoxide_triggers_emergency(self):
        detector = EscalationDetector()
        signal = detector.check("The carbon monoxide detector is going off")
        assert signal is not None
        assert signal.reason == EscalationReason.EMERGENCY

    def test_emergency_takes_priority_over_fair_housing(self):
        """Emergency check runs first — should return EMERGENCY even if Fair Housing
        keywords are also present."""
        detector = EscalationDetector()
        # Text with both emergency and fair housing words
        signal = detector.check("There's a fire and I'm asking about race")
        assert signal.reason == EscalationReason.EMERGENCY


class TestEscalationDetectorLegalFinancial:
    def test_sue_triggers_legal(self):
        detector = EscalationDetector()
        signal = detector.check("Can I sue the landlord for this?")
        assert signal is not None
        assert signal.reason == EscalationReason.LEGAL_QUESTION
        assert signal.urgency == "high"

    def test_lawsuit_triggers_legal(self):
        detector = EscalationDetector()
        signal = detector.check("I'm considering a lawsuit")
        assert signal is not None
        assert signal.reason == EscalationReason.LEGAL_QUESTION

    def test_attorney_triggers_legal(self):
        detector = EscalationDetector()
        signal = detector.check("I need to speak to my attorney about this")
        assert signal is not None
        assert signal.reason == EscalationReason.LEGAL_QUESTION

    def test_tax_advice_triggers_financial(self):
        detector = EscalationDetector()
        signal = detector.check("Can you give me tax advice on the rental deduction?")
        assert signal is not None
        assert signal.reason == EscalationReason.FINANCIAL_ADVICE_REQUESTED
        assert signal.urgency == "high"

    def test_investment_triggers_financial(self):
        detector = EscalationDetector()
        signal = detector.check("What's the investment ROI on this unit?")
        assert signal is not None
        assert signal.reason == EscalationReason.FINANCIAL_ADVICE_REQUESTED

    def test_legal_takes_priority_over_financial(self):
        """Legal check runs before financial."""
        detector = EscalationDetector()
        signal = detector.check("I want to sue and also get tax advice")
        assert signal.reason == EscalationReason.LEGAL_QUESTION


class TestEscalationDetectorEmotionalDistress:
    def test_frustration_marker_triggers_signal(self):
        detector = EscalationDetector()
        signal = detector.check("This is ridiculous, nobody helps me here")
        assert signal is not None
        # Emotional distress maps to UNKNOWN reason
        assert signal.reason == EscalationReason.UNKNOWN

    def test_demand_for_manager_triggers_signal(self):
        detector = EscalationDetector()
        signal = detector.check("Get me your manager right now")
        assert signal is not None

    def test_profanity_triggers_signal(self):
        detector = EscalationDetector()
        signal = detector.check("This is complete bullshit")
        assert signal is not None

    def test_mild_frustration_without_markers_no_signal(self):
        """Generic negative tone without specific markers should not trigger."""
        detector = EscalationDetector()
        signal = detector.check("I'm a bit unhappy with how this is going")
        assert signal is None


class TestEscalationDetectorToolFailures:
    def test_at_threshold_triggers(self):
        detector = EscalationDetector()
        signal = detector.check("hello", tool_failure_count=DEFAULT_TOOL_FAILURE_THRESHOLD)
        assert signal is not None
        assert signal.reason == EscalationReason.BACKEND_TOOL_FAILURE
        assert signal.urgency == "medium"

    def test_above_threshold_triggers(self):
        detector = EscalationDetector()
        signal = detector.check("hello", tool_failure_count=DEFAULT_TOOL_FAILURE_THRESHOLD + 2)
        assert signal is not None
        assert signal.reason == EscalationReason.BACKEND_TOOL_FAILURE

    def test_below_threshold_no_signal(self):
        detector = EscalationDetector()
        signal = detector.check("hello", tool_failure_count=DEFAULT_TOOL_FAILURE_THRESHOLD - 1)
        assert signal is None

    def test_zero_failures_no_signal(self):
        detector = EscalationDetector()
        signal = detector.check("Hello, I'd like some information", tool_failure_count=0)
        assert signal is None

    def test_custom_threshold_override(self):
        """Constructor override of threshold must be respected."""
        detector = EscalationDetector(tool_failure_threshold=1)
        signal = detector.check("hello", tool_failure_count=1)
        assert signal is not None
        assert signal.reason == EscalationReason.BACKEND_TOOL_FAILURE


class TestEscalationDetectorSafeText:
    def test_benign_leasing_inquiry_no_signal(self):
        detector = EscalationDetector()
        signal = detector.check("Hi, I'd like to learn about your available one-bedroom apartments")
        assert signal is None

    def test_tour_request_no_signal(self):
        detector = EscalationDetector()
        signal = detector.check("Can I schedule a tour for next Saturday?")
        assert signal is None

    def test_pricing_question_no_signal(self):
        detector = EscalationDetector()
        signal = detector.check("What is the monthly rent for a studio?")
        assert signal is None

    def test_maintenance_report_no_signal(self):
        """Routine maintenance (non-emergency) should not trigger escalation."""
        detector = EscalationDetector()
        signal = detector.check("My dishwasher is broken, can I submit a request?")
        assert signal is None


class TestEscalationSignal:
    def test_urgency_property_derives_from_reason(self):
        signal = EscalationSignal(
            reason=EscalationReason.EMERGENCY,
            notes="test",
        )
        assert signal.urgency == "emergency"

    def test_urgency_fair_housing_is_high(self):
        signal = EscalationSignal(
            reason=EscalationReason.FAIR_HOUSING_QUESTION,
            notes="test",
        )
        assert signal.urgency == "high"

    def test_urgency_tool_failure_is_medium(self):
        signal = EscalationSignal(
            reason=EscalationReason.BACKEND_TOOL_FAILURE,
            notes="test",
        )
        assert signal.urgency == "medium"
