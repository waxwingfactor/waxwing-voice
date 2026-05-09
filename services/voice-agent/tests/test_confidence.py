"""
Tests for ConfidenceEvaluator.

No audio, no backend, no LLM — pure text input to heuristic scorer.

Scenarios:
  - Hedging response triggers low confidence flag
  - Confident response stays unflagged
  - Empty response -> confidence 0.0
  - Multiple hedges compound penalties
  - Out-of-domain markers lower score
  - Question deflection (too many "?") lowers score
  - Threshold override in constructor works correctly
  - is_low property reflects threshold comparison
  - low_confidence_reason is None when confident
"""

from __future__ import annotations

import pytest

from voice_agent.conversation.confidence import (
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    ConfidenceEvaluator,
    ConfidenceResult,
)


class TestConfidenceEvaluatorHedging:
    def test_im_not_sure_lowers_score(self):
        """'I'm not sure' deducts 0.35 -> score 0.65, above default threshold 0.4."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("I'm not sure what the pet policy is.")
        assert result.confidence < 1.0
        assert result.confidence == pytest.approx(0.65, abs=0.01)

    def test_im_not_sure_triggers_low_with_lower_threshold(self):
        """With threshold=0.7, score 0.65 is below threshold -> is_low True."""
        evaluator = ConfidenceEvaluator(threshold=0.7)
        result = evaluator.evaluate("I'm not sure what the pet policy is.")
        assert result.is_low is True
        assert result.low_confidence_reason is not None

    def test_i_think_lowers_score(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("I think the rent is around $1,500 per month.")
        # "I think" alone is a 0.15 deduction — score should be 0.85, above threshold
        assert result.confidence < 1.0
        # Verify score decreased from 1.0
        assert result.confidence == pytest.approx(0.85, abs=0.01)

    def test_maybe_lowers_score_slightly(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("Maybe the gym is open on weekends.")
        assert result.confidence < 1.0

    def test_multiple_hedges_compound(self):
        """Two hedging phrases should compound penalties -> lower score."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "I'm not sure, but maybe the parking situation is different."
        )
        # "I'm not sure" (-0.35) + "maybe" (-0.12) = 0.53 -> below 0.7 threshold
        assert result.confidence < 0.7

    def test_multiple_hedges_trigger_low_with_matching_threshold(self):
        """With threshold=0.6, compound hedges score below it."""
        evaluator = ConfidenceEvaluator(threshold=0.6)
        result = evaluator.evaluate(
            "I'm not sure, but maybe the parking situation is different."
        )
        # 1.0 - 0.35 - 0.12 = 0.53 < 0.6
        assert result.is_low is True

    def test_strong_hedging_i_dont_know_lowers_score(self):
        """'I don't know' deducts 0.35 -> score 0.65, not low at default 0.4."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("I don't know the answer to that.")
        assert result.confidence == pytest.approx(0.65, abs=0.01)

    def test_strong_hedging_triggers_low_with_matching_threshold(self):
        """With threshold=0.7, 'I don't know' score 0.65 is low."""
        evaluator = ConfidenceEvaluator(threshold=0.7)
        result = evaluator.evaluate("I don't know the answer to that.")
        assert result.is_low is True

    def test_combined_hedges_trigger_default_threshold(self):
        """Three separate hedging phrases should push below 0.4."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "I'm not sure and I don't know, maybe it's something else entirely."
        )
        # -0.35 - 0.35 - 0.12 = 0.18 < 0.4
        assert result.is_low is True

    def test_i_cannot_confirm_lowers_score(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("I cannot confirm that information at this time.")
        assert result.confidence < 1.0


class TestConfidenceEvaluatorConfidentResponse:
    def test_direct_factual_answer_stays_high(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "The monthly rent for a one-bedroom is $1,450. "
            "Water and trash are included. Parking is $75 per month."
        )
        assert result.is_low is False
        assert result.confidence == pytest.approx(1.0, abs=0.01)
        assert result.low_confidence_reason is None

    def test_tour_booking_response_stays_high(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "I've booked a tour for you on Saturday at 10 AM. "
            "You'll receive a confirmation email shortly."
        )
        assert result.is_low is False

    def test_property_amenity_answer_stays_high(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "The property has a rooftop pool, gym, and co-working space. "
            "All amenities are available to residents 24/7."
        )
        assert result.is_low is False


class TestConfidenceEvaluatorOutOfDomain:
    def test_i_dont_have_that_information_lowers_score(self):
        """'I don't have that information' deducts 0.40 -> score 0.60, above default 0.4."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("I don't have that information available.")
        assert result.confidence < 1.0
        assert result.confidence == pytest.approx(0.60, abs=0.01)

    def test_i_dont_have_that_information_triggers_low_with_matching_threshold(self):
        """With threshold=0.65, score 0.60 is low."""
        evaluator = ConfidenceEvaluator(threshold=0.65)
        result = evaluator.evaluate("I don't have that information available.")
        assert result.is_low is True

    def test_not_in_my_system_lowers_score(self):
        """'not in my system' deducts 0.35 -> score 0.65."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("That's not in my system, I'm afraid.")
        assert result.confidence < 1.0

    def test_not_in_my_system_triggers_low_with_matching_threshold(self):
        evaluator = ConfidenceEvaluator(threshold=0.7)
        result = evaluator.evaluate("That's not in my system, I'm afraid.")
        assert result.is_low is True

    def test_please_contact_office_lowers_score(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("Please contact the leasing office for that.")
        # "please contact" is a mild out-of-domain signal (0.15 deduction)
        # Score 0.85 > 0.4 threshold -> not low, but score decreased.
        assert result.confidence < 1.0

    def test_short_redirect_with_redirect_pattern_lowers_score(self):
        """Short response + redirect pattern -> penalty applied."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("Please call the office.")
        # Short (< 10 words) + "please call" redirect pattern -> penalties applied
        assert result.confidence < 1.0

    def test_combined_out_of_domain_and_hedge_triggers_low(self):
        """Out-of-domain (-0.40) + hedging (-0.35) should push below 0.4."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "I'm not sure, and I don't have that information either."
        )
        # -0.35 (not sure) + -0.40 (don't have that info) = 0.25 < 0.4
        assert result.is_low is True


class TestConfidenceEvaluatorQuestionDeflection:
    def test_too_many_questions_lowers_score(self):
        """More than 2 question marks triggers deflection penalty."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "What did you mean? Could you clarify? Are you asking about rent? "
            "What exactly are you looking for?"
        )
        assert result.confidence < 1.0

    def test_two_questions_no_deflection_penalty(self):
        """Exactly 2 question marks should not trigger the 3-question threshold."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "Are you looking for a one or two bedroom? What's your move-in date?"
        )
        # Only 2 "?" — below the threshold of 3 — no deflection penalty.
        # Might still be 1.0 if no other hedges.
        # Score should not have the deflection deduction.
        # (We just verify it's not penalized for deflection — may still be 1.0)
        assert result.confidence >= 0.8


class TestConfidenceEvaluatorEdgeCases:
    def test_empty_response_zero_confidence(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("")
        assert result.confidence == 0.0
        assert result.is_low is True
        assert result.low_confidence_reason is not None

    def test_whitespace_only_response_zero_confidence(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("   ")
        assert result.confidence == 0.0

    def test_score_clamped_to_zero(self):
        """Many compounding penalties cannot push score below 0."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate(
            "I'm not sure. I don't know. I cannot confirm. Maybe. Perhaps. "
            "I don't have that information. Please contact someone else."
            "What? Why? Really? ?"
        )
        assert result.confidence >= 0.0

    def test_score_clamped_to_one(self):
        """No bonuses — score starts at 1.0 and can only decrease."""
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("The rent is $1,200 per month.")
        assert result.confidence <= 1.0

    def test_custom_threshold_lower(self):
        """With threshold=0.1, most responses stay above it."""
        evaluator = ConfidenceEvaluator(threshold=0.1)
        result = evaluator.evaluate("I think the rent is around $1,200.")
        # "I think" deducts 0.15 -> score 0.85 -> above threshold 0.1
        assert result.is_low is False

    def test_custom_threshold_higher(self):
        """With threshold=0.95, even minor hedges trigger low confidence."""
        evaluator = ConfidenceEvaluator(threshold=0.95)
        result = evaluator.evaluate("I think the rent is around $1,200.")
        # Score 0.85 < threshold 0.95
        assert result.is_low is True

    def test_threshold_stored_in_result(self):
        evaluator = ConfidenceEvaluator(threshold=0.3)
        result = evaluator.evaluate("The rent is $1,200.")
        assert result.threshold == 0.3


class TestConfidenceResult:
    def test_is_low_true_when_below_threshold(self):
        r = ConfidenceResult(confidence=0.3, low_confidence_reason="hedging", threshold=0.4)
        assert r.is_low is True

    def test_is_low_false_when_above_threshold(self):
        r = ConfidenceResult(confidence=0.5, low_confidence_reason=None, threshold=0.4)
        assert r.is_low is False

    def test_is_low_false_when_equal_to_threshold(self):
        """Exactly at threshold is NOT low (requires strictly less than)."""
        r = ConfidenceResult(confidence=0.4, low_confidence_reason=None, threshold=0.4)
        assert r.is_low is False

    def test_low_confidence_reason_none_when_above(self):
        evaluator = ConfidenceEvaluator()
        result = evaluator.evaluate("The monthly rent is $1,400.")
        assert result.low_confidence_reason is None
