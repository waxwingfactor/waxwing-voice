"""
SummaryBuilder — assembles SaveCallSummaryRequest from CallState at end of call.

Reads the CallState snapshot and derives each field of the backend contract.
Does NOT make LLM calls — the 'summary' field is either the LLM-generated text
already stored in CallState.ai_summary (populated by Gemini in Phase 5) or a
fallback derived purely from state machine data.

Output contract: SaveCallSummaryRequest in services/api/app/schemas/voice_tools.py:
  call_id:               str (UUID)
  summary:               str
  primary_intent:        str (CallerIntent value)
  sentiment:             Literal["positive","neutral","negative","frustrated"]
  action_items:          list[str]
  escalation_flag:       bool
  lead_fields_extracted: dict[str, Any]   (only non-None LeadFields values)
  next_steps:            str | None

Usage:
    builder = SummaryBuilder()
    payload = builder.build(state, state_machine)

SummaryBuilder is stateless — create one per service or per call, doesn't matter.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Literal

from voice_agent.state.call_state import CallPhase, CallState, EscalationReason

if TYPE_CHECKING:
    from voice_agent.conversation.state_machine import LeadCaptureStateMachine

log = logging.getLogger("voice_agent.conversation.summary_builder")

# Sentiment type alias matching Harsha's schema.
Sentiment = Literal["positive", "neutral", "negative", "frustrated"]

# ---------------------------------------------------------------------------
# Mapping from CallPhase to next-steps language.
# Covers the most common end-of-call states. Escalation and booking have
# their own dedicated logic below.
# ---------------------------------------------------------------------------

_PHASE_NEXT_STEPS: dict[CallPhase, str] = {
    CallPhase.CLOSING: "Call concluded normally. No further action required.",
    CallPhase.ENDED: "Call concluded normally. No further action required.",
    CallPhase.ESCALATION: "Call escalated to human agent. Follow up per handoff notes.",
    CallPhase.TOUR_BOOKING: "Tour booking was in progress at call end. Confirm with caller.",
    CallPhase.QUALIFICATION: "Lead capture was incomplete. Follow up to gather remaining fields.",
    CallPhase.RESIDENT_SUPPORT: "Resident support issue may be unresolved. Review transcript.",
    CallPhase.KNOWLEDGE_RETRIEVAL: "RAG retrieval was in progress at call end.",
    CallPhase.INTENT_DETECTION: "Call ended during intent detection. No action captured.",
    CallPhase.GREETING: "Call ended immediately after greeting. No intent captured.",
}


class SummaryBuilder:
    """
    Assembles the SaveCallSummaryRequest payload from CallState.

    The 'summary' text is:
      1. CallState.ai_summary if populated (Gemini output from Phase 5).
      2. Otherwise: a structured fallback built from state machine data.

    The 'primary_intent' is derived from the LeadCaptureStateMachine's
    recorded intent, which is more granular than CallState.intent for
    calls where the intent shifted mid-call.

    The 'sentiment' is taken directly from CallState.sentiment, which
    should be maintained by VoiceSession using the EscalationDetector's
    emotional distress signals.

    The 'next_steps' field is derived from the final call phase and
    whether any durable action was taken (booking confirmed, etc.).
    """

    def build(
        self,
        state: CallState,
        state_machine: "LeadCaptureStateMachine | None" = None,
    ) -> dict[str, Any]:
        """
        Build and return the SaveCallSummaryRequest payload dict.

        Args:
            state:         The CallState at end of call (after mark_ended()).
            state_machine: The LeadCaptureStateMachine if available — used to
                           derive primary_intent and next_steps more accurately.
                           Pass None to fall back to CallState fields only.

        Returns:
            Dict matching SaveCallSummaryRequest schema.
        """
        call_id = state.call_id

        # --- primary_intent ---
        if state_machine is not None:
            primary_intent = state_machine.primary_intent_for_summary()
        else:
            primary_intent = state.intent.value

        # --- summary text ---
        summary = self._build_summary_text(state, primary_intent)

        # --- sentiment ---
        sentiment: Sentiment = state.sentiment

        # --- action_items ---
        action_items = self._derive_action_items(state)

        # --- escalation_flag ---
        escalation_flag = state.escalation_flag

        # --- lead_fields_extracted ---
        lead_fields_extracted = state.lead_fields.to_backend_payload()

        # --- next_steps ---
        next_steps = self._derive_next_steps(state, state_machine)

        payload: dict[str, Any] = {
            "call_id": call_id,
            "summary": summary,
            "primary_intent": primary_intent,
            "sentiment": sentiment,
            "action_items": action_items,
            "escalation_flag": escalation_flag,
            "lead_fields_extracted": lead_fields_extracted,
            "next_steps": next_steps,
        }

        log.debug(
            "summary_builder.built",
            extra={
                "call_id": call_id,
                "primary_intent": primary_intent,
                "escalation_flag": escalation_flag,
                "field_count": len(lead_fields_extracted),
            },
        )

        return payload

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_summary_text(self, state: CallState, primary_intent: str) -> str:
        """
        Return the summary string.

        Priority:
          1. ai_summary — Gemini-generated (Phase 5)
          2. Structured fallback from state fields
        """
        if state.ai_summary:
            return state.ai_summary

        # Fallback: derive from state snapshot.
        parts: list[str] = []

        # Phase context
        phase_str = state.phase.value.replace("_", " ").capitalize()
        parts.append(f"Call ended in phase: {phase_str}.")

        # Intent
        intent_str = primary_intent.replace("_", " ")
        parts.append(f"Primary intent: {intent_str}.")

        # Lead fields captured
        captured = state.lead_fields.to_backend_payload()
        if captured:
            field_names = ", ".join(captured.keys())
            parts.append(f"Lead fields captured: {field_names}.")
        else:
            parts.append("No lead fields captured.")

        # Escalation
        if state.escalation_flag:
            reason = state.escalation_reason
            reason_str = reason.value.replace("_", " ") if reason else "unknown"
            parts.append(f"Escalated: yes (reason: {reason_str}).")
        else:
            parts.append("Escalated: no.")

        # Booking
        if state.booking_confirmed:
            parts.append("Tour booking confirmed.")
        elif state.selected_tour_slot:
            parts.append("Tour slot selected but booking not confirmed.")

        # Follow-up
        if state.follow_up_email_sent:
            parts.append("Follow-up email sent.")

        return " ".join(parts)

    def _derive_action_items(self, state: CallState) -> list[str]:
        """
        Derive a list of action items from state.

        Priority: use ai_action_items if already populated (Gemini Phase 5).
        Otherwise derive from state flags.
        """
        if state.ai_action_items:
            return list(state.ai_action_items)

        items: list[str] = []

        # Incomplete lead capture
        captured = state.lead_fields.to_backend_payload()
        missing_required = [
            f for f in ("name", "phone", "email") if f not in captured
        ]
        if missing_required:
            items.append(
                f"Follow up to capture missing lead fields: {', '.join(missing_required)}."
            )

        # Escalated call
        if state.escalation_flag:
            reason = state.escalation_reason
            if reason == EscalationReason.EMERGENCY:
                items.append("Emergency escalation — confirm resolution with caller.")
            elif reason in (
                EscalationReason.FAIR_HOUSING_QUESTION,
                EscalationReason.LEGAL_QUESTION,
            ):
                items.append("Review call for compliance; contact legal if needed.")
            else:
                items.append("Review escalated call and follow up with caller.")

        # Booking in progress
        if state.selected_tour_slot and not state.booking_confirmed:
            items.append("Tour slot was selected but not confirmed — follow up to book.")

        # Follow-up email pending
        if (
            state.lead_fields.email
            and state.lead_fields.email_confirmed
            and not state.follow_up_email_sent
        ):
            items.append("Send follow-up email to confirmed email address.")

        return items

    def _derive_next_steps(
        self,
        state: CallState,
        state_machine: "LeadCaptureStateMachine | None",
    ) -> str | None:
        """
        Derive next_steps string from the call's final state.

        Uses ai_next_steps if populated (Gemini Phase 5), otherwise
        derives from phase, booking status, and escalation flags.
        """
        if state.ai_next_steps:
            return state.ai_next_steps

        # Booking confirmed — primary success path.
        if state.booking_confirmed:
            slot = state.selected_tour_slot
            if slot:
                return (
                    f"Tour booked for {slot.date} at {slot.start_time}. "
                    "Send confirmation details to caller."
                )
            return "Tour booked. Confirm details with caller."

        # Escalated
        if state.escalation_flag:
            reason = state.escalation_reason
            if reason == EscalationReason.EMERGENCY:
                return "Emergency handoff completed. Verify caller safety and resolution."
            return (
                f"Call escalated ({reason.value if reason else 'unknown'}). "
                "Human agent to follow up."
            )

        # Incomplete lead capture
        captured = state.lead_fields.to_backend_payload()
        if state_machine is not None:
            missing = state_machine.captured_fields.missing()
        else:
            missing = [
                f for f in ("name", "phone", "email") if f not in captured
            ]

        if missing:
            return (
                f"Lead capture incomplete. Missing fields: {', '.join(missing)}. "
                "Follow up to gather remaining information."
            )

        # Default from phase.
        return _PHASE_NEXT_STEPS.get(state.phase)
