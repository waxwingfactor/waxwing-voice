"""
Lead-capture conversation state machine.

The machine drives the agent through a canonical call flow:

  GREETING
    |
    v
  INTENT_DETECTION          (detect leasing vs resident vs other)
    |
    +--[leasing/tour]---------> LEAD_CAPTURE
    |                               |
    |                               v
    |                           AWAITING_CONFIRMATION  <-- gate before durable actions
    |                               |   |
    |                               |   +--[refused / ambiguous]--> LEAD_CAPTURE
    |                               |
    |                               v
    |                           ACTION               (book_tour / save lead)
    |                               |
    |                               v
    +--[resident/support]------> KNOWLEDGE_RETRIEVAL
    |                               |
    |                               v
    |                           RESIDENT_SUPPORT
    |
    v
  CLOSING                    (wrap-up, optional follow-up email prompt)
    |
    v
  ENDED

  Any phase can jump to ESCALATION or ENDED on error/handoff.

Re-entrancy: advancing to a new phase never clears already-captured
LeadFields. If the caller jumps topics the captured data survives.

Design constraints:
  - No I/O: this module is pure Python logic. All I/O (backend calls,
    TTS) happens in VoiceSession which owns the state machine instance.
  - State machine is the ONLY place that sets CallState.phase.
  - Confirmation gate: any call to advance() toward ACTION when
    confirmation has not been obtained transitions to AWAITING_CONFIRMATION
    instead and records what needs confirming.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

log = logging.getLogger("voice_agent.conversation.state_machine")

# ---------------------------------------------------------------------------
# Internal conversation phase (distinct from CallPhase in call_state.py)
# ---------------------------------------------------------------------------
# We introduce ConversationPhase as the state machine's local view to keep
# the state machine self-contained. VoiceSession maps these to CallPhase when
# it updates CallState.


class ConversationPhase(str, Enum):
    """
    Internal phases of the lead-capture state machine.

    Maps to CallPhase values in call_state.py (same names for simplicity).
    VoiceSession.advance_call_phase() syncs them.
    """

    GREETING = "greeting"
    INTENT_DETECTION = "intent_detection"
    LEAD_CAPTURE = "lead_capture"              # active field collection
    AWAITING_CONFIRMATION = "awaiting_confirmation"  # gate before durable action
    ACTION = "action"                          # executing durable action
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    RESIDENT_SUPPORT = "resident_support"
    TOUR_BOOKING = "tour_booking"             # Phase 4: availability -> confirm -> book
    EMAIL_FOLLOWUP = "email_followup"         # Phase 4: email confirm + send
    CLOSING = "closing"
    ESCALATION = "escalation"
    ENDED = "ended"


# ---------------------------------------------------------------------------
# Detected caller intent (local enum — avoids import of call_state)
# ---------------------------------------------------------------------------


class DetectedIntent(str, Enum):
    UNKNOWN = "unknown"
    LEASING_INQUIRY = "leasing_inquiry"
    TOUR_REQUEST = "tour_request"
    MAINTENANCE = "maintenance"
    RESIDENT_SUPPORT = "resident_support"
    ESCALATION_REQUESTED = "escalation_requested"


# ---------------------------------------------------------------------------
# Lead field slot definitions
# ---------------------------------------------------------------------------

# Ordered list of fields the agent tries to capture for prospects.
# The machine asks for missing fields in this order.
LEAD_CAPTURE_FIELDS: list[str] = [
    "name",
    "phone",
    "email",
    "desired_unit_type",
    "move_in_date",
]

# Fields required before the confirmation gate will open.
# All five must be non-None for the machine to move to AWAITING_CONFIRMATION.
REQUIRED_FIELDS_FOR_CONFIRMATION: set[str] = {
    "name",
    "phone",
    "email",
    "desired_unit_type",
    "move_in_date",
}


# ---------------------------------------------------------------------------
# Confirmation request / result
# ---------------------------------------------------------------------------


class ConfirmationResult(str, Enum):
    """What the caller said in response to a confirmation prompt."""

    GIVEN = "given"          # Yes / correct / sounds good / confirmed
    REFUSED = "refused"      # No / that's wrong / cancel
    AMBIGUOUS = "ambiguous"  # Unclear — treated the same as REFUSED (conservative)


# Keyword-based heuristic for parsing confirmation responses.
# The lists are checked case-insensitively against the caller turn.

_CONFIRMATION_POSITIVE_PHRASES: list[str] = [
    "yes",
    "yeah",
    "yep",
    "correct",
    "that's right",
    "that is right",
    "sounds good",
    "looks good",
    "confirmed",
    "confirm",
    "go ahead",
    "book it",
    "do it",
    "perfect",
    "exactly",
    "right",
    "sure",
    "absolutely",
    "please do",
]

_CONFIRMATION_NEGATIVE_PHRASES: list[str] = [
    "no",
    "nope",
    "nah",
    "wrong",
    "that's wrong",
    "that is wrong",
    "incorrect",
    "not right",
    "cancel",
    "don't",
    "do not",
    "stop",
    "wait",
    "hold on",
    "change",
]


def _phrase_match(text: str, phrases: list[str]) -> bool:
    """
    Match a list of phrases against text using word-boundary matching for
    single-word phrases and substring matching for multi-word phrases.

    Prevents "no" from matching inside "dunno" or "know".
    """
    import re as _re
    for phrase in phrases:
        if " " in phrase:
            # Multi-word: simple substring match (spaces provide boundaries)
            if phrase in text:
                return True
        else:
            # Single word: require word boundary
            if _re.search(r"\b" + _re.escape(phrase) + r"\b", text):
                return True
    return False


def parse_confirmation(caller_text: str) -> ConfirmationResult:
    """
    Heuristic classification of a caller response to a confirmation prompt.

    Returns GIVEN if positive phrases are detected without negative.
    Returns REFUSED if negative phrases are detected.
    Returns AMBIGUOUS otherwise (treated as REFUSED by the state machine).

    Uses word-boundary matching to avoid false positives (e.g. "no" inside "dunno").
    """
    text = caller_text.lower().strip()
    has_positive = _phrase_match(text, _CONFIRMATION_POSITIVE_PHRASES)
    has_negative = _phrase_match(text, _CONFIRMATION_NEGATIVE_PHRASES)

    if has_negative:
        return ConfirmationResult.REFUSED
    if has_positive:
        return ConfirmationResult.GIVEN
    return ConfirmationResult.AMBIGUOUS


# ---------------------------------------------------------------------------
# Intent detection heuristic
# ---------------------------------------------------------------------------

_LEASING_KEYWORDS: list[str] = [
    "apartment",
    "apartments",
    "unit",
    "units",
    "rent",
    "rental",
    "renting",
    "lease",
    "leasing",
    "available",
    "availability",
    "move in",
    "move-in",
    "floor plan",
    "bedroom",
    "studio",
    "pricing",
    "price",
    "cost",
    "inquiry",
    "interested in",
    "looking for",
    "vacancies",
    "vacancy",
]

_TOUR_KEYWORDS: list[str] = [
    "tour",
    "visit",
    "see the apartment",
    "see the unit",
    "schedule",
    "appointment",
    "showing",
    "walk through",
    "walkthrough",
]

_MAINTENANCE_KEYWORDS: list[str] = [
    "maintenance",
    "repair",
    "broken",
    "leak",
    "fix",
    "issue",
    "problem",
    "not working",
    "heater",
    "ac",
    "air conditioning",
    "plumbing",
    "pest",
    "mold",
]

_RESIDENT_KEYWORDS: list[str] = [
    "resident",
    "tenant",
    "current tenant",
    "my unit",
    "my apartment",
    "package",
    "parking",
    "amenity",
    "amenities",
    "noise",
    "neighbor",
    "community",
]

_ESCALATION_KEYWORDS: list[str] = [
    "speak to a human",
    "speak to someone",
    "talk to a person",
    "talk to someone",
    "representative",
    "manager",
    "supervisor",
    "real person",
    "human agent",
    "customer service",
]


def _kw_match(text: str, keywords: list[str]) -> bool:
    """
    Return True if any keyword appears as a whole-word or phrase match in text.

    Uses simple boundary check: the character before and after the keyword
    must be a non-alphanumeric or the string edge. This prevents "rent" from
    matching inside "current" or "parent".

    Multi-word phrases (e.g. "move in") are matched as substrings because
    phrase boundaries are naturally delimited by surrounding words.
    """
    import re as _re
    for kw in keywords:
        if " " in kw:
            # Multi-word phrase: simple substring match is safe (spaces are boundaries)
            if kw in text:
                return True
        else:
            # Single word: require word boundary
            if _re.search(r"\b" + _re.escape(kw) + r"\b", text):
                return True
    return False


def detect_intent(caller_text: str) -> DetectedIntent:
    """
    Keyword-based intent classification.

    Checked in priority order: escalation > tour > leasing > maintenance > resident.
    Returns UNKNOWN if no keywords match.

    Uses whole-word matching for single-word keywords to avoid substring false
    positives (e.g. "rent" should not match inside "current").
    """
    text = caller_text.lower()

    if _kw_match(text, _ESCALATION_KEYWORDS):
        return DetectedIntent.ESCALATION_REQUESTED
    if _kw_match(text, _TOUR_KEYWORDS):
        return DetectedIntent.TOUR_REQUEST
    if _kw_match(text, _LEASING_KEYWORDS):
        return DetectedIntent.LEASING_INQUIRY
    if _kw_match(text, _MAINTENANCE_KEYWORDS):
        return DetectedIntent.MAINTENANCE
    if _kw_match(text, _RESIDENT_KEYWORDS):
        return DetectedIntent.RESIDENT_SUPPORT
    return DetectedIntent.UNKNOWN


# ---------------------------------------------------------------------------
# State machine transition output
# ---------------------------------------------------------------------------


@dataclass
class TransitionResult:
    """
    Output of a state machine advance() call.

    Attributes:
        new_phase:            The phase the machine moved to.
        intent:               Detected caller intent (may be UNKNOWN).
        missing_fields:       Fields still needed for lead capture.
        next_field_to_ask:    The next field the agent should ask for, or None.
        confirmation_needed:  True when the machine has entered AWAITING_CONFIRMATION.
        confirmation_summary: Human-readable summary of what needs confirming,
                              or None if not in confirmation state.
        action_blocked:       True when a durable action was attempted before
                              confirmation; the machine blocked it.
        confirmation_result:  Result of parsing caller confirmation response,
                              or None if this turn was not a confirmation.
        notes:                Optional free-text notes about the transition (for
                              internal tracing; not sent to caller).
    """

    new_phase: ConversationPhase
    intent: DetectedIntent = DetectedIntent.UNKNOWN
    missing_fields: list[str] = field(default_factory=list)
    next_field_to_ask: str | None = None
    confirmation_needed: bool = False
    confirmation_summary: str | None = None
    action_blocked: bool = False
    confirmation_result: ConfirmationResult | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Lead capture field registry
# ---------------------------------------------------------------------------


@dataclass
class CapturedFields:
    """
    Tracks which lead capture fields have been collected this call.

    Keys are field names from LEAD_CAPTURE_FIELDS.
    Values are the captured strings (raw; VoiceSession maps them to LeadFields types).

    Re-entrant: never cleared on phase transitions.
    """

    data: dict[str, Any] = field(default_factory=dict)

    def set(self, field_name: str, value: Any) -> None:
        """Record a captured field value. None values are accepted but not counted."""
        if value is not None:
            self.data[field_name] = value

    def get(self, field_name: str) -> Any:
        return self.data.get(field_name)

    def missing(self, required: set[str] | None = None) -> list[str]:
        """
        Return fields from required (or LEAD_CAPTURE_FIELDS) not yet captured.

        Preserves LEAD_CAPTURE_FIELDS order for consistent question ordering.
        """
        if required is None:
            required = set(LEAD_CAPTURE_FIELDS)
        return [f for f in LEAD_CAPTURE_FIELDS if f in required and f not in self.data]

    def all_required_present(self, required: set[str] | None = None) -> bool:
        if required is None:
            required = REQUIRED_FIELDS_FOR_CONFIRMATION
        return all(f in self.data for f in required)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.data)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class LeadCaptureStateMachine:
    """
    Finite-state machine for one inbound leasing or resident call.

    Usage in VoiceSession per-turn loop:

        result = sm.advance(caller_text=text, extracted_fields=fields)
        # Use result.new_phase, result.missing_fields, result.confirmation_needed
        # to drive the LLM prompt and tool calls.

    The machine does NOT make any I/O calls — it is pure logic.

    Confirmation gate:
        When all required lead fields are captured AND the caller has expressed
        intent to book a tour, advance() moves to AWAITING_CONFIRMATION instead
        of ACTION. The caller must verbally confirm before advance() will
        transition to ACTION. If the caller refuses or gives an ambiguous answer,
        the machine returns to LEAD_CAPTURE to re-collect incorrect fields.

    Phases (ConversationPhase):
        GREETING              -- initial state
        INTENT_DETECTION      -- classifying caller intent
        LEAD_CAPTURE          -- collecting name/phone/email/unit/move-in
        AWAITING_CONFIRMATION -- blocked; waiting for verbal confirm
        ACTION                -- durable action cleared to execute
        KNOWLEDGE_RETRIEVAL   -- RAG lookup in progress (Phase 3)
        RESIDENT_SUPPORT      -- resident Q&A
        CLOSING               -- wrap-up
        ESCALATION            -- human handoff
        ENDED                 -- call complete
    """

    def __init__(self) -> None:
        self._phase: ConversationPhase = ConversationPhase.GREETING
        self._intent: DetectedIntent = DetectedIntent.UNKNOWN
        self._fields: CapturedFields = CapturedFields()
        self._confirmation_pending: str | None = None  # what we're confirming
        self._phase_history: list[ConversationPhase] = [ConversationPhase.GREETING]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def phase(self) -> ConversationPhase:
        return self._phase

    @property
    def intent(self) -> DetectedIntent:
        return self._intent

    @property
    def captured_fields(self) -> CapturedFields:
        return self._fields

    @property
    def phase_history(self) -> list[ConversationPhase]:
        return list(self._phase_history)

    def advance(
        self,
        caller_text: str,
        extracted_fields: dict[str, Any] | None = None,
        action_ready: bool = False,
    ) -> TransitionResult:
        """
        Process one caller turn and advance the state machine.

        Args:
            caller_text:      Transcribed caller utterance.
            extracted_fields: Fields parsed from this turn (e.g. by the LLM).
                              Merged into CapturedFields immediately — never lost.
            action_ready:     Set True when VoiceSession has determined all
                              pre-conditions for a durable action (tour booking,
                              lead save) are satisfied. If confirmation has not
                              been obtained, the machine blocks into
                              AWAITING_CONFIRMATION.

        Returns:
            TransitionResult describing the new phase and what to do next.
        """
        # Merge any newly extracted fields first — always, regardless of phase.
        if extracted_fields:
            for fname, fval in extracted_fields.items():
                self._fields.set(fname, fval)

        result = self._handle_phase(caller_text, action_ready)
        if result.new_phase != self._phase:
            log.debug(
                "state_machine.transition",
                extra={
                    "from": self._phase.value,
                    "to": result.new_phase.value,
                    "intent": result.intent.value,
                },
            )
            self._phase = result.new_phase
            self._phase_history.append(result.new_phase)

        return result

    def force_phase(self, phase: ConversationPhase) -> None:
        """
        Directly set the phase — used by VoiceSession for escalation paths
        and ENDED transitions. Does NOT emit a TransitionResult.
        """
        if phase != self._phase:
            self._phase_history.append(phase)
        self._phase = phase

    def try_enter_tour_booking(self) -> bool:
        """
        Attempt to transition to TOUR_BOOKING phase.

        Called by VoiceSession when it detects tour intent + minimum required
        fields (name + phone OR name + email).

        Returns True if the transition was made, False if ineligible.
        Eligibility: currently in LEAD_CAPTURE and minimum fields present.
        """
        if self._phase != ConversationPhase.LEAD_CAPTURE:
            return False
        d = self._fields.as_dict()
        has_name = "name" in d
        has_phone_or_email = "phone" in d or "email" in d
        if has_name and has_phone_or_email:
            self.force_phase(ConversationPhase.TOUR_BOOKING)
            log.info(
                "state_machine.enter_tour_booking",
                extra={"fields_captured": list(d.keys())},
            )
            return True
        return False

    def try_enter_email_followup(self) -> bool:
        """
        Attempt to transition to EMAIL_FOLLOWUP phase.

        Called by VoiceSession at CLOSING transition when email follow-up
        is eligible: lead has email AND (tour booked OR follow-up agreed).

        Returns True if the transition was made.
        Eligibility: currently in LEAD_CAPTURE, ACTION, TOUR_BOOKING, or CLOSING.
        """
        eligible_phases = {
            ConversationPhase.LEAD_CAPTURE,
            ConversationPhase.ACTION,
            ConversationPhase.TOUR_BOOKING,
            ConversationPhase.CLOSING,
        }
        if self._phase not in eligible_phases:
            return False
        self.force_phase(ConversationPhase.EMAIL_FOLLOWUP)
        log.info("state_machine.enter_email_followup")
        return True

    def request_confirmation(self, summary: str) -> None:
        """
        Move to AWAITING_CONFIRMATION with a specific summary string.

        Called by VoiceSession when it wants the machine to gate an action.
        The summary text describes what is being confirmed (spoken to the caller).
        """
        self._confirmation_pending = summary
        self._phase = ConversationPhase.AWAITING_CONFIRMATION
        self._phase_history.append(ConversationPhase.AWAITING_CONFIRMATION)

    def resolve_confirmation(self, caller_text: str) -> TransitionResult:
        """
        Process a caller response to a confirmation prompt.

        Called by VoiceSession when phase == AWAITING_CONFIRMATION and
        the caller has just spoken.

        - GIVEN     -> ACTION
        - REFUSED   -> LEAD_CAPTURE (re-enter to correct fields)
        - AMBIGUOUS -> LEAD_CAPTURE (conservative: treat as refused)
        """
        result_code = parse_confirmation(caller_text)
        if result_code == ConfirmationResult.GIVEN:
            new_phase = ConversationPhase.ACTION
            notes = "Caller confirmed. Proceeding to action."
        else:
            new_phase = ConversationPhase.LEAD_CAPTURE
            notes = (
                "Caller refused or gave ambiguous answer. "
                "Re-entering lead capture to correct information."
            )

        if new_phase != self._phase:
            self._phase = new_phase
            self._phase_history.append(new_phase)
            self._confirmation_pending = None

        return TransitionResult(
            new_phase=new_phase,
            intent=self._intent,
            missing_fields=self._fields.missing(),
            next_field_to_ask=self._next_field(),
            confirmation_result=result_code,
            notes=notes,
        )

    def primary_intent_for_summary(self) -> str:
        """
        Derive the primary_intent string for SaveCallSummaryRequest.

        Uses phase history to pick the most meaningful intent even if the
        state machine ended in CLOSING or ESCALATION.
        """
        # Map DetectedIntent -> string value (matches CallerIntent in call_state.py)
        intent_map = {
            DetectedIntent.LEASING_INQUIRY: "leasing_inquiry",
            DetectedIntent.TOUR_REQUEST: "tour_request",
            DetectedIntent.MAINTENANCE: "maintenance",
            DetectedIntent.RESIDENT_SUPPORT: "resident_support",
            DetectedIntent.ESCALATION_REQUESTED: "escalation_requested",
            DetectedIntent.UNKNOWN: "unknown",
        }
        return intent_map.get(self._intent, "unknown")

    # ------------------------------------------------------------------
    # Private phase handlers
    # ------------------------------------------------------------------

    def _handle_phase(
        self, caller_text: str, action_ready: bool
    ) -> TransitionResult:
        phase = self._phase
        if phase == ConversationPhase.GREETING:
            return self._from_greeting(caller_text)
        if phase == ConversationPhase.INTENT_DETECTION:
            return self._from_intent_detection(caller_text)
        if phase == ConversationPhase.LEAD_CAPTURE:
            return self._from_lead_capture(caller_text, action_ready)
        if phase == ConversationPhase.AWAITING_CONFIRMATION:
            # Caller spoke while we're waiting for confirmation —
            # delegate to resolve_confirmation.
            return self.resolve_confirmation(caller_text)
        if phase == ConversationPhase.ACTION:
            # After action fires, move to CLOSING.
            return TransitionResult(
                new_phase=ConversationPhase.CLOSING,
                intent=self._intent,
                notes="Action complete. Moving to closing.",
            )
        if phase in (
            ConversationPhase.KNOWLEDGE_RETRIEVAL,
            ConversationPhase.RESIDENT_SUPPORT,
        ):
            return self._from_resident_or_knowledge(caller_text)
        if phase == ConversationPhase.CLOSING:
            return TransitionResult(
                new_phase=ConversationPhase.ENDED,
                intent=self._intent,
                notes="Closing acknowledged. Call ended.",
            )
        # ESCALATION, ENDED — terminal; stay put.
        return TransitionResult(new_phase=self._phase, intent=self._intent)

    def _from_greeting(self, caller_text: str) -> TransitionResult:
        """First real caller turn: move to INTENT_DETECTION then resolve intent."""
        intent = detect_intent(caller_text)
        if intent != DetectedIntent.UNKNOWN:
            self._intent = intent
        return self._route_by_intent(intent, caller_text)

    def _from_intent_detection(self, caller_text: str) -> TransitionResult:
        intent = detect_intent(caller_text)
        if intent != DetectedIntent.UNKNOWN:
            self._intent = intent
        return self._route_by_intent(intent, caller_text)

    def _route_by_intent(
        self, intent: DetectedIntent, caller_text: str
    ) -> TransitionResult:
        if intent == DetectedIntent.ESCALATION_REQUESTED:
            return TransitionResult(
                new_phase=ConversationPhase.ESCALATION,
                intent=intent,
                notes="Caller explicitly requested human agent.",
            )
        if intent in (DetectedIntent.LEASING_INQUIRY, DetectedIntent.TOUR_REQUEST):
            missing = self._fields.missing()
            next_f = missing[0] if missing else None
            return TransitionResult(
                new_phase=ConversationPhase.LEAD_CAPTURE,
                intent=intent,
                missing_fields=missing,
                next_field_to_ask=next_f,
                notes="Prospect inquiry detected. Starting lead capture.",
            )
        if intent in (DetectedIntent.MAINTENANCE, DetectedIntent.RESIDENT_SUPPORT):
            return TransitionResult(
                new_phase=ConversationPhase.RESIDENT_SUPPORT,
                intent=intent,
                notes="Resident support intent detected.",
            )
        # UNKNOWN — stay in intent detection for next turn
        return TransitionResult(
            new_phase=ConversationPhase.INTENT_DETECTION,
            intent=intent,
            notes="Intent unclear. Staying in intent detection.",
        )

    def _from_lead_capture(
        self, caller_text: str, action_ready: bool
    ) -> TransitionResult:
        """
        In lead capture: check if fields are complete or if intent shifts.

        If action_ready and all required fields are present:
            -> AWAITING_CONFIRMATION (gate)

        If a new intent (e.g. resident topic) is detected mid-capture:
            -> appropriate phase, but captured data is preserved.
        """
        # Re-check intent in case caller jumped topics.
        new_intent = detect_intent(caller_text)
        if new_intent not in (DetectedIntent.UNKNOWN, DetectedIntent.LEASING_INQUIRY):
            # Topic jump: honor it, but do NOT clear captured fields.
            if new_intent == DetectedIntent.ESCALATION_REQUESTED:
                return TransitionResult(
                    new_phase=ConversationPhase.ESCALATION,
                    intent=new_intent,
                    notes="Topic jump to escalation mid-capture. Fields preserved.",
                )
            if new_intent == DetectedIntent.TOUR_REQUEST:
                # Stay in lead capture — tour request is part of leasing flow.
                self._intent = new_intent
            elif new_intent in (
                DetectedIntent.MAINTENANCE,
                DetectedIntent.RESIDENT_SUPPORT,
            ):
                return TransitionResult(
                    new_phase=ConversationPhase.RESIDENT_SUPPORT,
                    intent=new_intent,
                    missing_fields=self._fields.missing(),
                    notes="Topic jump to resident support. Lead fields preserved.",
                )

        missing = self._fields.missing()
        next_f = missing[0] if missing else None

        if action_ready and self._fields.all_required_present():
            # All required fields present. Gate: confirmation required.
            summary = self._build_confirmation_summary()
            self._confirmation_pending = summary
            return TransitionResult(
                new_phase=ConversationPhase.AWAITING_CONFIRMATION,
                intent=self._intent,
                missing_fields=[],
                next_field_to_ask=None,
                confirmation_needed=True,
                confirmation_summary=summary,
                notes="All required fields captured. Entering confirmation gate.",
            )

        # Still collecting fields.
        return TransitionResult(
            new_phase=ConversationPhase.LEAD_CAPTURE,
            intent=self._intent,
            missing_fields=missing,
            next_field_to_ask=next_f,
            notes=f"Still collecting fields. Next: {next_f}",
        )

    def _from_resident_or_knowledge(self, caller_text: str) -> TransitionResult:
        """Resident/knowledge phase: check if caller switches back to leasing."""
        new_intent = detect_intent(caller_text)
        if new_intent in (DetectedIntent.LEASING_INQUIRY, DetectedIntent.TOUR_REQUEST):
            # Caller pivoting back to leasing — resume lead capture.
            self._intent = new_intent
            missing = self._fields.missing()
            return TransitionResult(
                new_phase=ConversationPhase.LEAD_CAPTURE,
                intent=new_intent,
                missing_fields=missing,
                next_field_to_ask=missing[0] if missing else None,
                notes="Caller returned to leasing inquiry. Resuming lead capture.",
            )
        if new_intent == DetectedIntent.ESCALATION_REQUESTED:
            return TransitionResult(
                new_phase=ConversationPhase.ESCALATION,
                intent=new_intent,
            )
        # Continue in resident support.
        return TransitionResult(
            new_phase=ConversationPhase.RESIDENT_SUPPORT,
            intent=self._intent,
            notes="Continuing resident support.",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _next_field(self) -> str | None:
        missing = self._fields.missing()
        return missing[0] if missing else None

    def _build_confirmation_summary(self) -> str:
        """
        Build a human-readable confirmation string from captured fields.

        This text is what the agent reads back to the caller before confirming
        the booking. It intentionally excludes internal-only fields.
        """
        parts: list[str] = []
        d = self._fields.as_dict()
        if "name" in d:
            parts.append(f"Name: {d['name']}")
        if "phone" in d:
            # Redact middle digits for read-back safety.
            raw = str(d["phone"])
            if len(raw) >= 4:
                parts.append(f"Phone: ***-{raw[-4:]}")
            else:
                parts.append(f"Phone: {raw}")
        if "email" in d:
            parts.append(f"Email: {d['email']}")
        if "desired_unit_type" in d:
            parts.append(f"Unit type: {d['desired_unit_type']}")
        if "move_in_date" in d:
            parts.append(f"Move-in: {d['move_in_date']}")
        return "; ".join(parts) if parts else "the information provided"
