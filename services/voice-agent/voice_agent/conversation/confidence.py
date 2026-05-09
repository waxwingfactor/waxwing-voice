"""
Low-confidence detection for LLM responses.

Analyzes text returned by Gemini (or any LLM stub) and produces:
  - confidence: float 0.0-1.0
  - low_confidence_reason: str | None (description when confidence is low)

Scoring is heuristic-only — no ML inference, no LLM call:
  - Hedging phrases                        -> score penalty
  - Agent asking questions instead of answering -> score penalty
  - Out-of-domain / refusal markers        -> score penalty
  - Short, deflecting responses            -> minor penalty
  - Contradictions detected (simple)       -> score penalty

The threshold for triggering escalation is configurable via Settings
(default LOW_CONFIDENCE_THRESHOLD = 0.4). ConfidenceEvaluator reads
from the Settings singleton by default, but accepts an override in
the constructor for test isolation.

Usage in VoiceSession:

    evaluator = ConfidenceEvaluator()
    result = evaluator.evaluate(llm_response_text)
    if result.is_low:
        state.trigger_escalation(EscalationReason.LOW_CONFIDENCE, result.reason)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("voice_agent.conversation.confidence")

# ---------------------------------------------------------------------------
# Default threshold
# ---------------------------------------------------------------------------

DEFAULT_LOW_CONFIDENCE_THRESHOLD: float = 0.4

# ---------------------------------------------------------------------------
# Hedging phrase patterns
# Each match deducts from the confidence score.
# Phrases ordered from strongest hedge (large deduction) to lighter hedge.
# ---------------------------------------------------------------------------

# (pattern, deduction)
_HEDGING_PATTERNS: list[tuple[str, float]] = [
    # Strong uncertainty
    (r"\bi('m| am) not sure\b", 0.35),
    (r"\bi don'?t know\b", 0.35),
    (r"\bi have no idea\b", 0.35),
    (r"\bi cannot (say|confirm|tell)\b", 0.30),
    (r"\bi can'?t (say|confirm|tell)\b", 0.30),
    (r"\buncertain\b", 0.25),
    (r"\bi'?m unsure\b", 0.25),
    (r"\bI'?m not certain\b", 0.25),
    # Medium uncertainty
    (r"\bi think\b", 0.15),
    (r"\bi believe\b", 0.15),
    (r"\bi suppose\b", 0.20),
    (r"\bpossibly\b", 0.15),
    (r"\bperhaps\b", 0.12),
    (r"\bmaybe\b", 0.12),
    (r"\bprobably\b", 0.10),
    (r"\bseems like\b", 0.10),
    (r"\bit (could|might) be\b", 0.10),
    # Soft hedges
    (r"\bI would (guess|imagine)\b", 0.15),
    (r"\bmy (best )?guess is\b", 0.15),
    (r"\bas far as i know\b", 0.12),
    (r"\bto the best of my knowledge\b", 0.10),
]

# ---------------------------------------------------------------------------
# Out-of-domain / refusal markers
# ---------------------------------------------------------------------------

_OUT_OF_DOMAIN_PATTERNS: list[tuple[str, float]] = [
    (r"\bi don'?t have (that |this )?(information|info|detail)\b", 0.40),
    (r"\bnot in my (system|data|database|knowledge)\b", 0.35),
    (r"\boutsid[e]? (my|the) (scope|capability|ability)\b", 0.35),
    (r"\bi'?m not able to (answer|help with|assist with) that\b", 0.30),
    (r"\bthat'?s (not something|something i can'?t|beyond what i)\b", 0.25),
    (r"\bI cannot (access|retrieve|look up)\b", 0.30),
    (r"\byou (should|would need to|may want to) (ask|contact|call|speak)\b", 0.20),
    (r"\bplease (contact|call|reach out to)\b", 0.15),
]

# ---------------------------------------------------------------------------
# Agent-is-asking-questions-instead-of-answering
# When LLM returns a response full of questions but the caller asked something,
# that is a deflection signal.
# ---------------------------------------------------------------------------

_QUESTION_DEFLECTION_THRESHOLD = 2  # more than this many "?" in response = penalty

_QUESTION_DEFLECTION_PENALTY: float = 0.20

# ---------------------------------------------------------------------------
# Contradiction markers (simple pattern)
# If the response contains contradictory phrases, score goes down.
# ---------------------------------------------------------------------------

_CONTRADICTION_PATTERNS: list[tuple[str, float]] = [
    (r"\bon the other hand\b.*\bbut\b", 0.10),
    (r"\bhowever\b.*\balso\b.*\bbut\b", 0.10),
    (r"\bi said .{0,40} but (actually|however)\b", 0.15),
]

# ---------------------------------------------------------------------------
# Short deflection (response < N words and includes a redirect)
# ---------------------------------------------------------------------------

_SHORT_RESPONSE_WORD_THRESHOLD = 10
_SHORT_RESPONSE_REDIRECT_PATTERNS: list[str] = [
    r"\bplease call\b",
    r"\bplease contact\b",
    r"\byou'?ll need to\b",
]
_SHORT_RESPONSE_PENALTY: float = 0.15


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ConfidenceResult:
    """
    Output of ConfidenceEvaluator.evaluate().

    Attributes:
        confidence:           0.0-1.0 (1.0 = fully confident, 0.0 = no confidence)
        low_confidence_reason: Human-readable explanation when confidence < threshold,
                               None otherwise.
        threshold:            The threshold used for this evaluation.
    """

    confidence: float
    low_confidence_reason: str | None
    threshold: float

    @property
    def is_low(self) -> bool:
        return self.confidence < self.threshold


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------


class ConfidenceEvaluator:
    """
    Scores LLM responses for uncertainty and low-confidence markers.

    All evaluation is deterministic keyword/regex matching — no external calls.

    Args:
        threshold: Override the confidence threshold. Useful in tests.
                   Defaults to DEFAULT_LOW_CONFIDENCE_THRESHOLD (0.4).
    """

    def __init__(self, threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD) -> None:
        self._threshold = threshold

    def evaluate(self, llm_response: str) -> ConfidenceResult:
        """
        Score the LLM response text for confidence.

        Returns a ConfidenceResult with confidence score and explanation.
        """
        if not llm_response or not llm_response.strip():
            # Empty response — treat as zero confidence.
            return ConfidenceResult(
                confidence=0.0,
                low_confidence_reason="Empty response from LLM.",
                threshold=self._threshold,
            )

        text = llm_response.lower()
        score: float = 1.0
        reasons: list[str] = []

        # 1. Hedging phrases.
        for pattern, deduction in _HEDGING_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score -= deduction
                reasons.append(f"hedging: '{pattern}'")

        # 2. Out-of-domain / refusal markers.
        for pattern, deduction in _OUT_OF_DOMAIN_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                score -= deduction
                reasons.append(f"out-of-domain: '{pattern}'")

        # 3. Question deflection (too many questions in the response).
        question_count = llm_response.count("?")
        if question_count > _QUESTION_DEFLECTION_THRESHOLD:
            score -= _QUESTION_DEFLECTION_PENALTY
            reasons.append(
                f"question deflection ({question_count} questions in response)"
            )

        # 4. Contradiction markers.
        for pattern, deduction in _CONTRADICTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                score -= deduction
                reasons.append(f"contradiction: '{pattern}'")

        # 5. Short deflection.
        word_count = len(llm_response.split())
        if word_count < _SHORT_RESPONSE_WORD_THRESHOLD:
            for pat in _SHORT_RESPONSE_REDIRECT_PATTERNS:
                if re.search(pat, text, re.IGNORECASE):
                    score -= _SHORT_RESPONSE_PENALTY
                    reasons.append("short response with redirect")
                    break

        # Clamp to [0.0, 1.0].
        score = max(0.0, min(1.0, score))

        low_reason: str | None = None
        if score < self._threshold:
            # Deduplicate reasons and cap list length for log brevity.
            unique_reasons = list(dict.fromkeys(reasons))[:5]
            low_reason = "; ".join(unique_reasons) if unique_reasons else "below threshold"
            log.info(
                "confidence.low",
                extra={
                    "score": round(score, 3),
                    "threshold": self._threshold,
                    "reasons": unique_reasons,
                },
            )

        return ConfidenceResult(
            confidence=round(score, 3),
            low_confidence_reason=low_reason,
            threshold=self._threshold,
        )
