"""
EscalationDetector — per-turn inspection for conditions requiring human handoff.

Runs on every caller turn BEFORE the LLM is called. If triggered, the caller
should be routed to a human immediately; no further LLM generation needed.

Detection categories (all keyword-based, no external ML):

  1. FAIR_HOUSING — race, religion, family status, disability, national origin,
     sex, color, creed, marital status, age.
     Why: Fair Housing Act prohibits discriminatory statements; agent must not
     engage. Escalation is mandatory, urgency HIGH.

  2. LEGAL / FINANCIAL — can I sue, is this legal, tax advice, attorney, etc.
     Urgency HIGH.

  3. EMOTIONAL_DISTRESS — frustration markers, repeated demands, profanity.
     Urgency MEDIUM.

  4. TOOL_FAILURE_THRESHOLD — 3+ backend tool failures in a single call.
     Urgency MEDIUM.

  5. EMERGENCY — fire, gas leak, break-in, medical emergency.
     Urgency EMERGENCY.

Design rules:
  - No I/O. EscalationDetector is pure Python.
  - All keyword lists live in module-level constants so they can be audited.
  - check() returns an EscalationSignal (dataclass) with reason + notes.
    Returning None means no escalation triggered.
  - The tool_failure threshold is configurable at construction time (default 3)
    so tests can lower it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from voice_agent.state.call_state import EscalationReason

log = logging.getLogger("voice_agent.conversation.escalation")

# ---------------------------------------------------------------------------
# Fair Housing keyword list (protected characteristics)
# ---------------------------------------------------------------------------
# Source: 42 U.S.C. § 3604 + HUD guidance on protected classes.
# These words in caller text → mandatory escalation, no exceptions.

FAIR_HOUSING_KEYWORDS: list[str] = [
    # Protected classes (explicit)
    "race",
    "racial",
    "color",
    "colour",
    "religion",
    "religious",
    "church",
    "mosque",
    "synagogue",
    "national origin",
    "nationality",
    "country of origin",
    "sex",
    "gender",
    "familial status",
    "family status",
    "children",
    "pregnant",
    "pregnancy",
    "disability",
    "disabled",
    "handicap",
    "handicapped",
    "wheelchair",
    # Discriminatory intent signals
    "only rent to",
    "prefer tenants",
    "prefer residents",
    "no kids",
    "no children",
    "quiet building",         # code for "no children" in context
    "what kind of people",
    "what type of people",
    "neighborhood demographics",
    "school district",        # sometimes used as proxy for demographics
    "crime in the area",      # can be proxies for race/national origin
    "safe neighborhood",
    "section 8",
    "housing voucher",
    "voucher",
]

# ---------------------------------------------------------------------------
# Legal and financial escalation keywords
# ---------------------------------------------------------------------------

LEGAL_KEYWORDS: list[str] = [
    "sue",
    "lawsuit",
    "legal action",
    "attorney",
    "lawyer",
    "court",
    "litigation",
    "is this legal",
    "illegal",
    "violating the law",
    "my rights",
    "housing authority",
    "file a complaint",
    "discrimination complaint",
    "hud complaint",
    "civil rights",
]

FINANCIAL_KEYWORDS: list[str] = [
    "tax",
    "tax advice",
    "tax deduction",
    "write off",
    "write-off",
    "financial advice",
    "investment",
    "roi",
    "return on investment",
    "depreciation",
    "1031",
]

# ---------------------------------------------------------------------------
# Emergency keywords
# ---------------------------------------------------------------------------

EMERGENCY_KEYWORDS: list[str] = [
    "fire",
    "on fire",
    "smoke",
    "gas leak",
    "gas smell",
    "smell gas",
    "carbon monoxide",
    "explosion",
    "break in",
    "break-in",
    "breaking in",
    "intruder",
    "someone broke in",
    "robbery",
    "flood",
    "flooding",
    "sewage overflow",
    "medical emergency",
    "heart attack",
    "call 911",
    "ambulance",
    "collapsed",
    "unconscious",
    "not breathing",
    "choking",
]

# ---------------------------------------------------------------------------
# Emotional distress / frustration markers
# ---------------------------------------------------------------------------

FRUSTRATION_MARKERS: list[str] = [
    "this is ridiculous",
    "this is insane",
    "this is unacceptable",
    "terrible service",
    "worst",
    "completely useless",
    "completely unhelpful",
    "this is a joke",
    "what a joke",
    "i've been waiting",
    "i have been waiting",
    "i keep calling",
    "nobody helps",
    "no one helps",
    "i demand",
    "i need to speak",
    "get me a manager",
    "get me your manager",
    "let me speak to",
    "i want to speak",
    "i am so frustrated",
    "i'm so frustrated",
    "i'm furious",
    "i am furious",
    "i'm angry",
    "i am angry",
    "fed up",
    "sick of this",
    "done with this",
    "screw this",
    "forget it",
    "this is bull",
    "absolute garbage",
]

PROFANITY_MARKERS: list[str] = [
    "damn",
    "hell",
    "crap",
    "ass ",
    " ass",
    "asshole",
    "bullshit",
    "bull shit",
    "fuck",
    "shit",
    "bitch",
    "bastard",
    "idiot",
    "moron",
    "stupid",
    "incompetent",
]

# Default tool failure count before escalation triggers.
DEFAULT_TOOL_FAILURE_THRESHOLD = 3


# ---------------------------------------------------------------------------
# Signal type
# ---------------------------------------------------------------------------


@dataclass
class EscalationSignal:
    """
    Indicates that human handoff is required.

    Attributes:
        reason:  The EscalationReason to set on CallState.
        notes:   Free-text context for the human agent receiving the handoff.
        urgency: Derived from reason.to_handoff_urgency() — included here
                 for convenience so VoiceSession doesn't re-derive it.
    """

    reason: EscalationReason
    notes: str

    @property
    def urgency(self) -> str:
        return self.reason.to_handoff_urgency().value


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class EscalationDetector:
    """
    Stateless checker that inspects a caller turn for escalation triggers.

    Usage:
        detector = EscalationDetector()
        signal = detector.check(caller_text, tool_failure_count)
        if signal:
            state.trigger_escalation(signal.reason, signal.notes)
            await client.request_human_handoff(...)

    tool_failure_threshold can be overridden in tests.
    """

    def __init__(self, tool_failure_threshold: int = DEFAULT_TOOL_FAILURE_THRESHOLD) -> None:
        self._threshold = tool_failure_threshold

    @staticmethod
    def _match(text: str, keywords: list[str]) -> list[str]:
        """
        Return matched keywords from the list using word-boundary matching
        for single words and substring matching for multi-word phrases.

        Prevents "hell" from matching inside "hello", "no" inside "know", etc.
        """
        import re as _re
        triggered = []
        for kw in keywords:
            if " " in kw or kw.endswith(" ") or kw.startswith(" "):
                # Multi-word phrase or space-padded: use as-is (spaces are boundaries)
                kw_stripped = kw.strip()
                if kw_stripped in text:
                    triggered.append(kw_stripped)
            else:
                # Single word: require word boundary
                if _re.search(r"\b" + _re.escape(kw) + r"\b", text):
                    triggered.append(kw)
        return triggered

    def check(
        self,
        caller_text: str,
        tool_failure_count: int = 0,
    ) -> EscalationSignal | None:
        """
        Inspect a caller turn and the running tool failure count.

        Checks are run in priority order — EMERGENCY first, Fair Housing second,
        legal/financial third, emotional distress fourth, tool failures last.

        Returns EscalationSignal if escalation is needed, else None.
        """
        text = caller_text.lower()

        # 1. Emergency — highest priority.
        signal = self._check_emergency(text)
        if signal:
            log.warning(
                "escalation.emergency",
                extra={"notes": signal.notes},
            )
            return signal

        # 2. Fair Housing.
        signal = self._check_fair_housing(text)
        if signal:
            log.warning(
                "escalation.fair_housing",
                extra={"notes": signal.notes},
            )
            return signal

        # 3. Legal / financial.
        signal = self._check_legal_financial(text)
        if signal:
            log.warning(
                "escalation.legal_financial",
                extra={"notes": signal.notes},
            )
            return signal

        # 4. Emotional distress.
        signal = self._check_emotional_distress(text)
        if signal:
            log.info(
                "escalation.emotional_distress",
                extra={"notes": signal.notes},
            )
            return signal

        # 5. Repeated tool failures.
        if tool_failure_count >= self._threshold:
            notes = (
                f"Backend tool failures hit threshold "
                f"({tool_failure_count} >= {self._threshold}). "
                "Routing to human to complete the caller's request."
            )
            log.warning(
                "escalation.tool_failures",
                extra={"count": tool_failure_count, "threshold": self._threshold},
            )
            return EscalationSignal(
                reason=EscalationReason.BACKEND_TOOL_FAILURE,
                notes=notes,
            )

        return None

    # ------------------------------------------------------------------
    # Private category checks
    # ------------------------------------------------------------------

    def _check_emergency(self, text: str) -> EscalationSignal | None:
        triggered = self._match(text, EMERGENCY_KEYWORDS)
        if triggered:
            return EscalationSignal(
                reason=EscalationReason.EMERGENCY,
                notes=f"Emergency keyword(s) detected: {triggered}. Immediate handoff required.",
            )
        return None

    def _check_fair_housing(self, text: str) -> EscalationSignal | None:
        triggered = self._match(text, FAIR_HOUSING_KEYWORDS)
        if triggered:
            return EscalationSignal(
                reason=EscalationReason.FAIR_HOUSING_QUESTION,
                notes=(
                    f"Fair Housing keyword(s) detected: {triggered}. "
                    "Agent must not answer; escalating immediately."
                ),
            )
        return None

    def _check_legal_financial(self, text: str) -> EscalationSignal | None:
        legal_triggered = self._match(text, LEGAL_KEYWORDS)
        if legal_triggered:
            return EscalationSignal(
                reason=EscalationReason.LEGAL_QUESTION,
                notes=f"Legal keyword(s) detected: {legal_triggered}.",
            )
        financial_triggered = self._match(text, FINANCIAL_KEYWORDS)
        if financial_triggered:
            return EscalationSignal(
                reason=EscalationReason.FINANCIAL_ADVICE_REQUESTED,
                notes=f"Financial keyword(s) detected: {financial_triggered}.",
            )
        return None

    def _check_emotional_distress(self, text: str) -> EscalationSignal | None:
        frustration_triggered = self._match(text, FRUSTRATION_MARKERS)
        profanity_triggered = self._match(text, PROFANITY_MARKERS)
        all_triggered = frustration_triggered + profanity_triggered
        if all_triggered:
            return EscalationSignal(
                reason=EscalationReason.UNKNOWN,  # emotional distress -> unknown category
                notes=(
                    f"Caller distress markers detected: {all_triggered[:5]}. "
                    "Routing to human for empathetic handling."
                ),
            )
        return None
