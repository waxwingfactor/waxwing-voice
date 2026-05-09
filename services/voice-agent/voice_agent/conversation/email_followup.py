"""
FollowUpEmailCoordinator — manages the EMAIL_FOLLOWUP phase.

This phase fires automatically near the end of an eligible call during the
CLOSING transition. It is NOT caller-requested — the agent initiates it.

Eligibility (checked by VoiceSession before entering this phase):
  - Lead captured with valid email (LeadFields.email is not None)
  - AND at least one of:
    - Tour was booked (CallState.booking_confirmed is True)
    - Caller agreed to receive a follow-up (captured by conversation context)

Sub-state machine:
  CONFIRMING_EMAIL     -> agent reads email letter-by-letter; caller confirms/corrects
  AWAITING_CORRECTION  -> caller is spelling correction; agent re-reads
  SENDING              -> email_confirmed=True; call send_follow_up_email
  EMAIL_SENT           -> success
  EMAIL_FAILED         -> non-retryable failure or retries exhausted
  SKIPPED              -> eligibility check failed; phase exited silently

Safety invariants:
  - send_follow_up_email is NEVER called unless email_confirmed=True.
  - If send fails, we NEVER claim the email was sent.
  - After a retryable failure we retry once; then escalate.
  - Non-retryable failure: escalate immediately.

Email address read-back:
  The coordinator spells the email address character-by-character.
  Example: "akhil@2ispeed.com" is read as:
    "a-k-h-i-l at 2-i-s-p-e-e-d dot com"

Template selection:
  - booking_confirmed=True  -> EmailTemplateType.tour_confirmation
  - booking_confirmed=False -> EmailTemplateType.follow_up

Usage in VoiceSession:

    if self.state_machine.phase == ConversationPhase.EMAIL_FOLLOWUP:
        email_result = await self._email_coordinator.handle_turn(
            caller_text=caller_text,
            call_id=self._effective_call_id(),
            property_id=uuid.UUID(self.state.property_id),
            lead_id=uuid.UUID(self.state.lead_id),
            email=self.state.lead_fields.email,
            booking_confirmed=self.state.booking_confirmed,
        )
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from voice_agent.conversation.state_machine import ConfirmationResult, parse_confirmation

if TYPE_CHECKING:
    from voice_agent.tools.backend_client import BackendClient, EmailTemplateType

log = logging.getLogger("voice_agent.conversation.email_followup")

MAX_EMAIL_RETRIES: int = 1


# ---------------------------------------------------------------------------
# Sub-states
# ---------------------------------------------------------------------------


class EmailSubState(str, Enum):
    CONFIRMING_EMAIL = "confirming_email"
    AWAITING_CORRECTION = "awaiting_correction"
    SENDING = "sending"
    EMAIL_SENT = "email_sent"
    EMAIL_FAILED = "email_failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class EmailTurnResult:
    """
    Output of FollowUpEmailCoordinator.handle_turn().

    Attributes:
        sub_state:       Current sub-state after this turn.
        done:            True when coordinator is finished (sent, failed, skipped).
        email_sent:      True only when send_follow_up_email succeeded.
        email_confirmed: True when the caller confirmed the address.
        escalate:        True when failure requires human handoff.
        escalation_reason: Why.
        agent_prompt:    Suggested agent utterance for this turn.
        notes:           Internal trace (not spoken).
    """

    sub_state: EmailSubState
    done: bool = False
    email_sent: bool = False
    email_confirmed: bool = False
    escalate: bool = False
    escalation_reason: str | None = None
    agent_prompt: str | None = None
    notes: str | None = None


# ---------------------------------------------------------------------------
# Email readback formatting
# ---------------------------------------------------------------------------

_SPECIAL_CHAR_NAMES: dict[str, str] = {
    "@": "at",
    ".": "dot",
    "-": "dash",
    "_": "underscore",
    "+": "plus",
}


def format_email_for_readback(email: str) -> str:
    """
    Format an email address for spoken letter-by-letter readback.

    Example:
        "akhil@2ispeed.com"
        -> "a-k-h-i-l at 2-i-s-p-e-e-d dot com"

    Strategy:
      - Split on "@" first.
      - Within each segment, spell alphanumeric characters with hyphens.
      - Expand "." within domain as "dot".
      - Expand "@" as "at".
    """
    if "@" not in email:
        return _spell_segment(email)

    local_part, domain = email.split("@", 1)
    local_spelled = _spell_segment(local_part)
    domain_spelled = _spell_domain(domain)
    return f"{local_spelled} at {domain_spelled}"


def _spell_segment(segment: str) -> str:
    """Spell a segment (local part) char-by-char with hyphens between chars."""
    chars = []
    for ch in segment:
        chars.append(_SPECIAL_CHAR_NAMES.get(ch, ch))
    return "-".join(chars)


def _spell_domain(domain: str) -> str:
    """
    Spell domain with "dot" for "." separators.
    e.g. "2ispeed.com" -> "2-i-s-p-e-e-d dot c-o-m"
    """
    parts = domain.split(".")
    spelled_parts = [_spell_segment(p) for p in parts]
    return " dot ".join(spelled_parts)


# ---------------------------------------------------------------------------
# Template selection
# ---------------------------------------------------------------------------


def _select_template(booking_confirmed: bool) -> "EmailTemplateType":
    from voice_agent.tools.backend_client import EmailTemplateType
    if booking_confirmed:
        return EmailTemplateType.tour_confirmation
    return EmailTemplateType.follow_up


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class FollowUpEmailCoordinator:
    """
    Manages the EMAIL_FOLLOWUP phase for one call.

    One instance lives per VoiceSession. Activated only when eligibility
    is confirmed by VoiceSession at CLOSING transition.
    """

    def __init__(self, client: "BackendClient") -> None:
        self._client = client
        self._sub_state: EmailSubState = EmailSubState.CONFIRMING_EMAIL
        self._pending_email: str = ""
        self._email_confirmed: bool = False
        self._booking_confirmed: bool = False
        self._retry_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def sub_state(self) -> EmailSubState:
        return self._sub_state

    def start(self, email: str, booking_confirmed: bool) -> EmailTurnResult:
        """
        Initialize the coordinator with the email to confirm.
        Returns the first agent prompt (read the email back to the caller).
        Should be called by VoiceSession when entering EMAIL_FOLLOWUP phase.
        """
        self._pending_email = email
        self._booking_confirmed = booking_confirmed
        self._sub_state = EmailSubState.CONFIRMING_EMAIL
        self._email_confirmed = False

        readback = format_email_for_readback(email)
        return EmailTurnResult(
            sub_state=self._sub_state,
            agent_prompt=(
                f"Before I let you go, I'd like to send you a confirmation. "
                f"I have your email as: {readback}. Is that correct?"
            ),
            notes=f"Started email confirmation for address (redacted for log).",
        )

    async def handle_turn(
        self,
        caller_text: str,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> EmailTurnResult:
        """
        Process one caller turn while in EMAIL_FOLLOWUP phase.
        """
        state = self._sub_state

        if state == EmailSubState.CONFIRMING_EMAIL:
            return await self._from_confirming(caller_text, call_id, property_id, lead_id)

        if state == EmailSubState.AWAITING_CORRECTION:
            return self._from_awaiting_correction(caller_text)

        # Terminal states
        return EmailTurnResult(
            sub_state=self._sub_state,
            done=True,
            email_sent=(self._sub_state == EmailSubState.EMAIL_SENT),
            email_confirmed=self._email_confirmed,
            notes="Terminal state; no further turns.",
        )

    # ------------------------------------------------------------------
    # Sub-state handlers
    # ------------------------------------------------------------------

    async def _from_confirming(
        self,
        caller_text: str,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> EmailTurnResult:
        """
        Caller is responding to the email read-back.
        GIVEN -> send the email.
        REFUSED / AMBIGUOUS -> ask for correction.
        """
        result = parse_confirmation(caller_text)

        if result == ConfirmationResult.GIVEN:
            self._email_confirmed = True
            return await self._send_email(call_id, property_id, lead_id)

        # Not confirmed — ask for the correct address.
        self._sub_state = EmailSubState.AWAITING_CORRECTION
        return EmailTurnResult(
            sub_state=self._sub_state,
            agent_prompt="Got it — what's the correct email address?",
            notes="Caller did not confirm email; awaiting correction.",
        )

    def _from_awaiting_correction(self, caller_text: str) -> EmailTurnResult:
        """
        Caller has spelled out the corrected email. Capture it and
        re-read it back to confirm.

        In a real implementation the LLM would extract the email from
        caller_text. Here we store the raw text and read it back as-is,
        which is correct for voice (the caller said the address).
        """
        # Simple extraction: look for something with "@" in caller_text.
        # Falls back to storing the full turn if no "@" found.
        corrected = _extract_email_from_text(caller_text)

        if corrected:
            self._pending_email = corrected
        else:
            # Could not extract — store full text, re-read it.
            self._pending_email = caller_text.strip()

        self._sub_state = EmailSubState.CONFIRMING_EMAIL
        readback = format_email_for_readback(self._pending_email)
        return EmailTurnResult(
            sub_state=self._sub_state,
            agent_prompt=(
                f"Let me read that back: {readback}. Is that right?"
            ),
            notes="Email corrected; re-entering CONFIRMING_EMAIL.",
        )

    async def _send_email(
        self,
        call_id: uuid.UUID,
        property_id: uuid.UUID,
        lead_id: uuid.UUID,
    ) -> EmailTurnResult:
        """
        Safety-critical: email is only sent when email_confirmed=True.
        On success: EMAIL_SENT.
        On retryable failure: retry once, then EMAIL_FAILED + escalate.
        On non-retryable: EMAIL_FAILED + escalate immediately.
        """
        from voice_agent.tools.backend_client import BackendToolError

        template = _select_template(self._booking_confirmed)

        for attempt in range(1, MAX_EMAIL_RETRIES + 2):
            try:
                response = await self._client.send_follow_up_email(
                    property_id=property_id,
                    lead_id=lead_id,
                    call_id=call_id,
                    template_type=template,
                    context={"email_confirmed": True},
                )
                # SUCCESS
                self._sub_state = EmailSubState.EMAIL_SENT
                log.info(
                    "email_followup.sent",
                    extra={
                        "template": template.value,
                        "delivery_status": response.delivery_status,
                        # recipient email not logged — PII
                    },
                )
                return EmailTurnResult(
                    sub_state=self._sub_state,
                    done=True,
                    email_sent=True,
                    email_confirmed=True,
                    agent_prompt=(
                        "Great — I've sent a confirmation to that address. "
                        "Is there anything else I can help you with?"
                    ),
                    notes=f"Email sent on attempt {attempt}.",
                )

            except BackendToolError as exc:
                if not exc.retryable or attempt > MAX_EMAIL_RETRIES:
                    log.error(
                        "email_followup.failed",
                        extra={
                            "tool_name": "send_follow_up_email",
                            "attempt": attempt,
                            "error_code": exc.code,
                            "retryable": exc.retryable,
                            "retry_decision": "escalate",
                        },
                    )
                    self._sub_state = EmailSubState.EMAIL_FAILED
                    return EmailTurnResult(
                        sub_state=self._sub_state,
                        done=True,
                        email_sent=False,
                        email_confirmed=self._email_confirmed,
                        escalate=True,
                        escalation_reason="email_send_failed",
                        agent_prompt=(
                            "I wasn't able to send that email right now. "
                            "Our team will follow up with the confirmation details."
                        ),
                        notes=f"send_follow_up_email failed (code={exc.code}, attempt={attempt}).",
                    )

                log.warning(
                    "email_followup.retry",
                    extra={
                        "tool_name": "send_follow_up_email",
                        "attempt": attempt,
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                        "retry_decision": "retry",
                    },
                )

        # Should not reach here
        self._sub_state = EmailSubState.EMAIL_FAILED
        return EmailTurnResult(
            sub_state=self._sub_state,
            done=True,
            email_sent=False,
            escalate=True,
            escalation_reason="email_send_failed",
            agent_prompt=(
                "I wasn't able to send that email. "
                "Our team will reach out to confirm the details."
            ),
            notes="Exhausted all email send attempts.",
        )


# ---------------------------------------------------------------------------
# Email extraction helper
# ---------------------------------------------------------------------------


def _extract_email_from_text(text: str) -> str | None:
    """
    Attempt to extract a plausible email address from caller text.
    Returns the first token containing "@", or None if not found.

    This is intentionally simple — in Phase 5 the LLM extraction will
    handle complex cases (spelled-out "at" signs, etc.).
    """
    for token in text.split():
        if "@" in token and "." in token.split("@")[-1]:
            return token.strip(".,!?;")
    return None
