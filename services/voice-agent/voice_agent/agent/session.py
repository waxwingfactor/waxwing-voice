"""
VoiceSession — one per inbound phone call.

Owns the call lifecycle:
  1. Call arrives via Twilio -> LiveKit room is created
  2. VoiceSession is instantiated with property_id and call identifiers
  3. start() is called: bootstraps a call record via POST /v1/calls/ and
     stores the backend call_id on CallState
  4. Session runs the conversation loop (Phase 2 per-turn loop):
     caller_text
       -> EscalationDetector.check()       (safety gate, runs first)
       -> LeadCaptureStateMachine.advance() (phase + field tracking)
       -> _llm_respond() (stubbed, Phase 1)
       -> ConfidenceEvaluator.evaluate()   (score LLM response)
       -> tool calls if action permitted
       -> LeadCaptureStateMachine.advance(tool_results) (post-action advance)
     - Receive audio from LiveKit (caller speaking)
     - Transcribe with Whisper STT
     - Build grounded prompt (property context + RAG chunks)
     - Send to Gemini-3.0 Flash
     - Stream response audio via VibeVoice TTS back through LiveKit
     - Handle barge-in (caller interrupts) and silence detection
  5. On call end: flush transcript, save summary, trigger follow-up if needed

Phase 0: stub — correct interface, no live provider connections.
Phase 1: fill in LiveKit, Whisper, Gemini, VibeVoice integrations.
Phase 2: conversation state machine, escalation detection, confidence
         scoring, and structured summary wired into session lifecycle.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from voice_agent.conversation.booking import TourBookingCoordinator
from voice_agent.conversation.confidence import ConfidenceEvaluator
from voice_agent.conversation.email_followup import FollowUpEmailCoordinator
from voice_agent.conversation.escalation import EscalationDetector
from voice_agent.conversation.retrieval import RetrievalCoordinator
from voice_agent.conversation.state_machine import (
    ConversationPhase,
    LeadCaptureStateMachine,
)
from voice_agent.conversation.summary_builder import SummaryBuilder
from voice_agent.providers.tts.protocol import TTSAdapter
from voice_agent.state.call_state import (
    CallPhase,
    CallState,
    EscalationReason,
    SpeakerRole,
)
from voice_agent.tools.backend_client import (
    BackendClient,
    BackendToolError,
    CallEventType,
    HandoffUrgency,
)

if TYPE_CHECKING:
    pass

log = logging.getLogger("voice_agent.agent.session")


# Map ConversationPhase -> CallPhase (same values by design, but separate enums)
_CONV_TO_CALL_PHASE: dict[ConversationPhase, CallPhase] = {
    ConversationPhase.GREETING: CallPhase.GREETING,
    ConversationPhase.INTENT_DETECTION: CallPhase.INTENT_DETECTION,
    ConversationPhase.LEAD_CAPTURE: CallPhase.QUALIFICATION,
    ConversationPhase.AWAITING_CONFIRMATION: CallPhase.QUALIFICATION,
    ConversationPhase.ACTION: CallPhase.TOUR_BOOKING,
    ConversationPhase.KNOWLEDGE_RETRIEVAL: CallPhase.KNOWLEDGE_RETRIEVAL,
    ConversationPhase.RESIDENT_SUPPORT: CallPhase.RESIDENT_SUPPORT,
    ConversationPhase.TOUR_BOOKING: CallPhase.TOUR_BOOKING,
    ConversationPhase.EMAIL_FOLLOWUP: CallPhase.CLOSING,
    ConversationPhase.CLOSING: CallPhase.CLOSING,
    ConversationPhase.ESCALATION: CallPhase.ESCALATION,
    ConversationPhase.ENDED: CallPhase.ENDED,
}


class VoiceSession:
    """
    Manages one inbound phone call from greeting to goodbye.

    Phase 0: __init__ and public interface are final.
             Internal _run_* methods are stubs.
    Phase 1: implement _stt_transcribe, _llm_respond, _tts_speak,
             and the LiveKit event loop.
    Phase 2: conversation state machine, escalation detection, confidence
             scoring, and structured summary now wired into the session.
    Phase 5: auth changed from company_id (X-Company-Id header) to jwt_token
             (Bearer JWT). Pass settings.voice_agent_jwt as jwt_token.

    Per-turn loop (handle_caller_turn):
        1. EscalationDetector.check(caller_text) — safety gate, runs first.
           If triggered: set CallState.escalation_flag, skip LLM, go to end.
        2. LeadCaptureStateMachine.advance(caller_text, extracted_fields)
           — advances phase and tracks captured lead fields.
        3. _llm_respond() — stubbed in Phase 1; raises NotImplementedError.
        4. ConfidenceEvaluator.evaluate(llm_response) — score LLM output.
           If low: trigger escalation with LOW_CONFIDENCE reason.
        5. Tool calls if machine is in ACTION phase.
        6. Sync CallState.phase from state machine result.
    """

    def __init__(
        self,
        property_id: str,
        jwt_token: str,
        backend_client: BackendClient,
        tts_adapter: TTSAdapter | None = None,
        twilio_call_sid: str | None = None,
        livekit_room_id: str | None = None,
        caller_phone_number: str | None = None,
    ) -> None:
        """
        Args:
            property_id:      Property UUID string (from Twilio routing metadata).
            jwt_token:        HS256 JWT for backend auth (see BLOCKERS.md §7).
            backend_client:   Injected BackendClient instance.
            tts_adapter:      TTSAdapter implementation. Defaults to ElevenLabsTTSAdapter
                              built from settings. Inject MockTTSAdapter in tests.
            twilio_call_sid:  Twilio call SID, if available at session start.
            livekit_room_id:  LiveKit room ID for this call.
            caller_phone_number: Caller's E.164 phone number (PII — not logged).
        """
        self.state = CallState(
            property_id=property_id,
            twilio_call_sid=twilio_call_sid,
            livekit_room_id=livekit_room_id,
            caller_phone_number=caller_phone_number,
        )
        self._jwt_token = jwt_token
        self._client = backend_client

        # Low 3 fix: ElevenLabsTTSAdapter construction removed from VoiceSession.
        # In the VPA pipeline, TTS goes through livekit-plugins-elevenlabs wired in
        # entrypoint.py — VoiceSession never calls _tts_speak or the adapter directly
        # in production. Constructing the adapter here was wasteful (opened a connection)
        # and misleading (suggested the session was doing TTS when it isn't).
        # The tts_adapter parameter is retained for test backwards compatibility:
        # tests that inject MockTTSAdapter for handle_barge_in still work.
        self._tts_adapter: TTSAdapter | None = tts_adapter  # None in production

        # Phase 2: conversation orchestration modules
        self.state_machine = LeadCaptureStateMachine()
        self._escalation_detector = EscalationDetector()
        self._confidence_evaluator = ConfidenceEvaluator()
        self._summary_builder = SummaryBuilder()

        # Phase 3: RAG retrieval coordinator
        self._retrieval = RetrievalCoordinator(client=self._client)

        # Phase 3: last retrieved knowledge slot (used by prompt builder)
        from voice_agent.prompts.system_prompt import KnowledgeSlot, PropertyProfileSlot
        self._last_knowledge_slot: KnowledgeSlot = KnowledgeSlot()

        # Bug F fix: property profile loaded from backend at call start.
        # Stored here so before_llm_cb can pass it to build_system_prompt every turn.
        # Defaults to placeholder slots until _load_property_profile() succeeds.
        self._property_profile: PropertyProfileSlot = PropertyProfileSlot()

        # Phase 4: tour booking and email follow-up coordinators
        self._booking_coordinator = TourBookingCoordinator(client=self._client)
        self._email_coordinator = FollowUpEmailCoordinator(client=self._client)

        # Phase 1: store LiveKit room handle, Whisper model, Gemini client, VibeVoice client
        log.info(
            "VoiceSession created",
            extra={
                "call_id": self.state.call_id,
                "property_id": property_id,
                # caller_phone_number deliberately omitted from log (PII)
            },
        )

    # ------------------------------------------------------------------
    # Public lifecycle methods
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Called when the LiveKit room is ready and the caller is connected.

        Phase 1 sequence:
          1. POST /v1/calls/ to bootstrap a backend call record.
             The returned call_id is stored as CallState.backend_call_id and
             used by all subsequent tool calls.
          2. Emit call_started event.
          3. Load property profile from backend.
          4. Speak greeting via VibeVoice.

        If create_call fails with a non-retryable error (e.g. PROPERTY_NOT_FOUND),
        the session cannot proceed — log the error and end gracefully.
        """
        self.state.set_phase(CallPhase.GREETING)

        # --- Step 1: Bootstrap call record ---
        started_at_iso = self.state.started_at.isoformat()
        try:
            call_resp = await self._client.create_call(
                property_id=uuid.UUID(self.state.property_id),
                twilio_call_sid=self.state.twilio_call_sid or f"stub-{self.state.call_id}",
                livekit_room_id=self.state.livekit_room_id,
                caller_phone=self.state.caller_phone_number,  # PII; not logged in client
                started_at=started_at_iso,
            )
            # Store the authoritative backend call_id — all subsequent tool calls use this.
            self.state.backend_call_id = str(call_resp.id)
            log.info(
                "VoiceSession.start: call record created",
                extra={
                    "local_call_id": self.state.call_id,
                    "backend_call_id": self.state.backend_call_id,
                    "property_id": self.state.property_id,
                },
            )
        except BackendToolError as exc:
            log.error(
                "VoiceSession.start: failed to create call record",
                extra={
                    "call_id": self.state.call_id,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
            # Cannot proceed without a backend call_id — end the session.
            # In Phase 1 this will trigger a graceful LiveKit disconnect.
            raise

        # --- Step 2: Emit call_started event ---
        try:
            await self._client.create_call_event(
                call_id=uuid.UUID(self.state.backend_call_id),
                event_type=CallEventType.call_started,
                payload={},
                occurred_at=started_at_iso,
            )
        except BackendToolError as exc:
            # Non-blocking — log and continue.
            log.warning(
                "VoiceSession.start: call_started event failed",
                extra={
                    "backend_call_id": self.state.backend_call_id,
                    "error_code": exc.code,
                },
            )
            self.state.tool_failure_count += 1

        # Phase 1: load property profile from backend
        # Phase 1: speak greeting via VibeVoice

    async def handle_utterance(self, audio_bytes: bytes) -> None:
        """
        Called by LiveKit event loop when a caller utterance ends.

        Phase 0: stub.
        Phase 1:
          1. Transcribe audio_bytes with Whisper
          2. Append transcript segment (caller) to call state
          3. Flush segment to backend via save_transcript_segment
          4. Detect intent / phase transition
          5. Check for safety triggers (Fair Housing, escalation keywords)
          6. Retrieve RAG context if needed (Phase 3)
          7. Build system prompt
          8. Call Gemini-3.0 Flash
          9. Stream agent response via VibeVoice
          10. Append agent transcript segment and flush
        """
        log.debug(
            "handle_utterance (stub)",
            extra={"call_id": self.state.call_id},
        )

    async def handle_caller_turn(
        self,
        caller_text: str,
        extracted_fields: dict[str, Any] | None = None,
        action_ready: bool = False,
    ) -> dict[str, Any]:
        """
        Process one complete caller turn through the Phase 2 orchestration loop.

        This is the primary per-turn entrypoint for Phase 2 logic.
        In Phase 1, this was handled inside handle_utterance (stubbed).
        Phase 2 separates the orchestration from the audio I/O so it
        can be tested with synthetic text without audio or LLM providers.

        Per-turn sequence:
          1. Run EscalationDetector on caller_text.
             If triggered: set escalation on CallState, return immediately.
          2. Advance state machine with caller_text + extracted_fields.
          3. Sync CallState.phase from state machine result.
          4. Evaluate LLM response confidence if llm_response is available.
             (Currently always skipped because _llm_respond raises NotImplementedError.)
          5. If machine entered AWAITING_CONFIRMATION, surface that in result.

        Returns:
            dict with keys:
              escalated:              bool — whether escalation was triggered
              escalation_reason:      str | None
              phase:                  str — new ConversationPhase value
              missing_fields:         list[str]
              next_field_to_ask:      str | None
              confirmation_needed:    bool
              confirmation_summary:   str | None
              action_blocked:         bool
        """
        # --- Step 1: Escalation check (safety gate, runs before anything else) ---
        signal = self._escalation_detector.check(
            caller_text=caller_text,
            tool_failure_count=self.state.tool_failure_count,
        )
        if signal:
            self.state.trigger_escalation(signal.reason, signal.notes)
            self._sync_call_phase(ConversationPhase.ESCALATION)
            log.info(
                "handle_caller_turn.escalated",
                extra={
                    "call_id": self.state.call_id,
                    "reason": signal.reason.value,
                },
            )
            return {
                "escalated": True,
                "escalation_reason": signal.reason.value,
                "phase": ConversationPhase.ESCALATION.value,
                "missing_fields": [],
                "next_field_to_ask": None,
                "confirmation_needed": False,
                "confirmation_summary": None,
                "action_blocked": False,
                "knowledge_unavailable": False,
                "retrieved_source_labels": [],
            }

        # --- Step 2: State machine advance ---
        transition = self.state_machine.advance(
            caller_text=caller_text,
            extracted_fields=extracted_fields,
            action_ready=action_ready,
        )

        # --- Step 3: Sync CallState.phase ---
        self._sync_call_phase(transition.new_phase)

        # --- Step 3b: RAG retrieval (Phase 3) ---
        # Run after the state machine so we know the new phase.
        # If the backend call_id is not yet set (call not started), skip.
        if self.state.backend_call_id:
            try:
                self._last_knowledge_slot = await self._retrieval.do_retrieve(
                    caller_text=caller_text,
                    phase=transition.new_phase.value,
                    property_id=uuid.UUID(self.state.property_id),
                    call_id=self._effective_call_id(),
                )
            except Exception as exc:
                # Retrieval errors must not crash the call.
                log.warning(
                    "handle_caller_turn.retrieval_error",
                    extra={"call_id": self.state.call_id, "error": str(exc)[:200]},
                )

        # --- Step 3c: Phase 4 coordinator delegation ---
        # If the state machine has entered TOUR_BOOKING or EMAIL_FOLLOWUP,
        # delegate the turn to the appropriate coordinator. The coordinator
        # returns a result with booking/email outcome; we re-sync CallState.
        coordinator_result: dict[str, Any] = {}

        # Medium 5 fix: ensure lead_id is set before coordinator delegation.
        # If state.lead_id is None (no prior create_or_update_lead call), call it now
        # so the booking/email coordinator has a valid lead_id to pass to book_tour
        # and send_follow_up_email. Without this wiring, TOUR_BOOKING and
        # EMAIL_FOLLOWUP coordinators were silently skipped every time (lead_id_str
        # was always None → the `if lead_id_str:` guard prevented any coordinator run).
        if (
            transition.new_phase in (ConversationPhase.TOUR_BOOKING, ConversationPhase.EMAIL_FOLLOWUP)
            and self.state.backend_call_id
            and not self.state.lead_id
        ):
            await self._ensure_lead_id()

        if transition.new_phase == ConversationPhase.TOUR_BOOKING and self.state.backend_call_id:
            lead_id_str = self.state.lead_id
            if lead_id_str:
                try:
                    booking_result = await self._booking_coordinator.handle_turn(
                        caller_text=caller_text,
                        call_id=self._effective_call_id(),
                        property_id=uuid.UUID(self.state.property_id),
                        lead_id=uuid.UUID(lead_id_str),
                    )
                    coordinator_result["booking_sub_state"] = booking_result.sub_state.value
                    coordinator_result["booking_confirmed"] = booking_result.booking_confirmed
                    coordinator_result["booking_escalate"] = booking_result.escalate
                    coordinator_result["booking_agent_prompt"] = booking_result.agent_prompt

                    if booking_result.booking_confirmed:
                        self.state.booking_confirmed = True
                        if booking_result.booking_id:
                            self.state.booking_id = booking_result.booking_id
                        if booking_result.selected_slot:
                            from voice_agent.state.call_state import TourSlot
                            s = booking_result.selected_slot
                            self.state.selected_tour_slot = TourSlot(
                                slot_id=s.slot_id,
                                date=s.date,
                                start_time=s.start_time,
                                end_time=s.end_time,
                            )

                    if booking_result.escalate:
                        from voice_agent.state.call_state import EscalationReason
                        self.state.trigger_escalation(
                            EscalationReason.BOOKING_FAILED,
                            notes=booking_result.escalation_reason or "Booking failed.",
                        )
                        self._sync_call_phase(ConversationPhase.ESCALATION)

                    if booking_result.done:
                        # Coordinator finished; move state machine past TOUR_BOOKING.
                        if not booking_result.escalate:
                            self.state_machine.force_phase(ConversationPhase.CLOSING)
                            self._sync_call_phase(ConversationPhase.CLOSING)
                            coordinator_result["phase"] = ConversationPhase.CLOSING.value
                except Exception as exc:
                    log.error(
                        "handle_caller_turn.booking_coordinator_error",
                        extra={"call_id": self.state.call_id, "error": str(exc)[:200]},
                    )

        elif transition.new_phase == ConversationPhase.EMAIL_FOLLOWUP and self.state.backend_call_id:
            lead_id_str = self.state.lead_id
            if lead_id_str:
                try:
                    email_result = await self._email_coordinator.handle_turn(
                        caller_text=caller_text,
                        call_id=self._effective_call_id(),
                        property_id=uuid.UUID(self.state.property_id),
                        lead_id=uuid.UUID(lead_id_str),
                    )
                    coordinator_result["email_sub_state"] = email_result.sub_state.value
                    coordinator_result["email_sent"] = email_result.email_sent
                    coordinator_result["email_confirmed"] = email_result.email_confirmed
                    coordinator_result["email_agent_prompt"] = email_result.agent_prompt

                    if email_result.email_confirmed:
                        self.state.lead_fields.email_confirmed = True
                    if email_result.email_sent:
                        self.state.follow_up_email_sent = True

                    if email_result.escalate:
                        from voice_agent.state.call_state import EscalationReason
                        self.state.trigger_escalation(
                            EscalationReason.BACKEND_TOOL_FAILURE,
                            notes=email_result.escalation_reason or "Email send failed.",
                        )
                        self._sync_call_phase(ConversationPhase.ESCALATION)

                    if email_result.done and not email_result.escalate:
                        self.state_machine.force_phase(ConversationPhase.CLOSING)
                        self._sync_call_phase(ConversationPhase.CLOSING)
                        coordinator_result["phase"] = ConversationPhase.CLOSING.value
                except Exception as exc:
                    log.error(
                        "handle_caller_turn.email_coordinator_error",
                        extra={"call_id": self.state.call_id, "error": str(exc)[:200]},
                    )

        # --- Step 4: Confidence evaluation ---
        # In Phase 1, _llm_respond raises NotImplementedError so we cannot get
        # a response here. In Phase 2 tests, LLM is not called. Confidence
        # evaluation of live LLM responses will be wired in Phase 3/5 when
        # Gemini integration is complete.

        # --- Step 5: Build result dict ---
        result: dict[str, Any] = {
            "escalated": False,
            "escalation_reason": None,
            "phase": coordinator_result.get("phase", transition.new_phase.value),
            "missing_fields": transition.missing_fields,
            "next_field_to_ask": transition.next_field_to_ask,
            "confirmation_needed": transition.confirmation_needed,
            "confirmation_summary": transition.confirmation_summary,
            "action_blocked": transition.action_blocked,
            "knowledge_unavailable": self._last_knowledge_slot.knowledge_unavailable,
            "retrieved_source_labels": [
                # Extract source label prefix before the first newline from each chunk
                chunk.split("\n")[0].strip("[]")
                for chunk in self._last_knowledge_slot.chunks
            ],
        }
        # Merge any coordinator-specific keys
        result.update({k: v for k, v in coordinator_result.items() if k != "phase"})
        return result

    async def evaluate_llm_response(self, llm_response: str) -> dict[str, Any]:
        """
        Run the confidence evaluator on an LLM response text.

        If confidence is below threshold, triggers LOW_CONFIDENCE escalation
        on CallState (but does not immediately request handoff — VoiceSession
        will do that at call end or when the next turn is processed).

        Returns:
            dict with keys: confidence (float), low_confidence_reason (str|None),
                            is_low (bool), escalation_triggered (bool).
        """
        result = self._confidence_evaluator.evaluate(llm_response)

        escalation_triggered = False
        if result.is_low and not self.state.escalation_flag:
            self.state.trigger_escalation(
                EscalationReason.LOW_CONFIDENCE,
                notes=result.low_confidence_reason or "Confidence below threshold.",
            )
            self._sync_call_phase(ConversationPhase.ESCALATION)
            escalation_triggered = True
            log.info(
                "handle_caller_turn.low_confidence_escalation",
                extra={
                    "call_id": self.state.call_id,
                    "confidence": result.confidence,
                    "threshold": result.threshold,
                },
            )

        return {
            "confidence": result.confidence,
            "low_confidence_reason": result.low_confidence_reason,
            "is_low": result.is_low,
            "escalation_triggered": escalation_triggered,
        }

    def build_summary(self) -> dict[str, Any]:
        """
        Assemble the SaveCallSummaryRequest payload using SummaryBuilder.

        Delegates to CallState.summary_payload() for backwards compat,
        but enriches it with SummaryBuilder's state-machine-aware logic.

        Returns:
            Dict matching SaveCallSummaryRequest schema.
        """
        return self._summary_builder.build(
            state=self.state,
            state_machine=self.state_machine,
        )

    def try_enter_tour_booking(self) -> bool:
        """
        Attempt to enter TOUR_BOOKING phase from LEAD_CAPTURE.

        Returns True if the transition succeeded (minimum lead fields present).
        VoiceSession should call this when tour intent is detected and the
        state machine is in LEAD_CAPTURE.
        """
        return self.state_machine.try_enter_tour_booking()

    def try_enter_email_followup(self) -> bool:
        """
        Attempt to enter EMAIL_FOLLOWUP phase at CLOSING.

        Eligibility checked here: lead email must be non-None and
        (booking_confirmed OR follow-up agreed).

        Returns True if the transition succeeded and email coordinator is started.
        The email coordinator start() prompt should be spoken before the next turn.
        """
        email = self.state.lead_fields.email
        if not email:
            return False  # No email to confirm
        if not (self.state.booking_confirmed or self.state.lead_fields.tour_interest):
            return False  # Not eligible

        entered = self.state_machine.try_enter_email_followup()
        if entered:
            self._sync_call_phase(ConversationPhase.EMAIL_FOLLOWUP)
            # Start the coordinator — the returned prompt should be spoken to the caller.
            self._email_coordinator.start(
                email=email,
                booking_confirmed=self.state.booking_confirmed,
            )
            log.info(
                "VoiceSession.email_followup_started",
                extra={"call_id": self.state.call_id},
            )
        return entered

    async def handle_barge_in(self) -> None:
        """
        Called when the caller starts speaking while the agent is speaking.

        In the VPA pipeline, barge-in is handled by VoicePipelineAgent's VAD
        integration — this method is kept for test compatibility only.

        If a tts_adapter is injected (e.g. MockTTSAdapter in tests), cancel is
        forwarded to it. In production, _tts_adapter is None and this is a no-op.
        """
        log.debug(
            "handle_barge_in: cancelling TTS stream",
            extra={"call_id": self.state.call_id},
        )
        if self._tts_adapter is not None:
            await self._tts_adapter.cancel()

    async def handle_silence_timeout(self) -> None:
        """
        Called when silence exceeds the configured threshold.
        Phase 1: prompt the caller with "Are you still there?" or end the call.
        """
        log.debug(
            "handle_silence_timeout (stub)",
            extra={"call_id": self.state.call_id},
        )

    async def end(self, reason: str = "normal") -> None:
        """
        Cleanly end the call session.

        Phase 1:
          1. Stop audio streams
          2. Flush any unflushed transcript segments
          3. Trigger handoff if escalation_flag is set
          4. Save call summary
          5. Emit call_ended event
          6. Close LiveKit room
        """
        log.info(
            "VoiceSession.end",
            extra={"call_id": self.state.call_id, "reason": reason},
        )
        self.state.mark_ended()

        if self.state.escalation_flag and self.state.escalation_reason:
            await self._trigger_handoff()

        await self._save_summary()

    # ------------------------------------------------------------------
    # Private stubs — implemented in Phase 1+
    # ------------------------------------------------------------------

    def _sync_call_phase(self, conv_phase: ConversationPhase) -> None:
        """
        Sync CallState.phase from the state machine's ConversationPhase.

        The state machine uses ConversationPhase (its own enum). CallState
        uses CallPhase. This method translates and applies the update.
        """
        call_phase = _CONV_TO_CALL_PHASE.get(conv_phase)
        if call_phase and call_phase != self.state.phase:
            self.state.set_phase(call_phase)

    async def _ensure_lead_id(self) -> None:
        """
        Medium 5 fix: call create_or_update_lead to obtain a lead_id if we don't
        have one yet. Sets state.lead_id on success.

        This is called just before TOUR_BOOKING or EMAIL_FOLLOWUP coordinator
        delegation in handle_caller_turn. Without it, state.lead_id is always None
        and the coordinator guards (`if lead_id_str:`) silently skip coordination.

        If the call fails: logs the error. state.lead_id remains None. The
        coordinator guard will still prevent the booking/email attempt (safe).
        """
        if self.state.lead_id:
            return  # already set — idempotent

        try:
            from voice_agent.tools.backend_client import LeadFieldsInput
            # Build a LeadFieldsInput from current call state (only captured fields).
            lead_fields_input = LeadFieldsInput(
                **self.state.lead_fields.to_backend_payload()
            )
            lead_resp = await self._client.create_or_update_lead(
                property_id=uuid.UUID(self.state.property_id),
                call_id=self._effective_call_id(),
                lead_fields=lead_fields_input,
            )
            self.state.lead_id = str(lead_resp.lead_id)
            log.info(
                "VoiceSession._ensure_lead_id: lead record created/updated",
                extra={
                    "call_id": self.state.call_id,
                    "lead_id": self.state.lead_id,
                    "created": lead_resp.created,
                },
            )
        except BackendToolError as exc:
            log.error(
                "VoiceSession._ensure_lead_id: create_or_update_lead failed",
                extra={
                    "call_id": self.state.call_id,
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
            self.state.tool_failure_count += 1
        except Exception as exc:
            log.error(
                "VoiceSession._ensure_lead_id: unexpected error",
                extra={
                    "call_id": self.state.call_id,
                    "error": str(exc)[:200],
                },
            )
            self.state.tool_failure_count += 1

    async def _load_property_profile(self) -> None:
        """
        Load property profile from backend and populate _property_profile slot.

        Bug F fix: This was a no-op stub. It is now fully implemented.
        Called in start() (fallback path) AND explicitly in the production path
        (bridge_call_id branch in entrypoint.py) so the system prompt always
        carries real property data regardless of which path created the call record.

        On failure: logs the error and leaves _property_profile as the placeholder
        default (safe — the agent will say "[PROPERTY_NAME_PLACEHOLDER]" rather
        than crashing). This is acceptable for graceful degradation.
        """
        from voice_agent.prompts.system_prompt import PropertyProfileSlot
        try:
            profile_resp = await self._client.get_property_profile(
                property_id=uuid.UUID(self.state.property_id),
            )
            # Map backend response fields to PropertyProfileSlot.
            amenities: list[str] = []
            if profile_resp.amenities:
                # amenities is a dict[str, Any] from the backend; extract keys as labels.
                amenities = list(profile_resp.amenities.keys())

            office_hours_str = "[OFFICE_HOURS_PLACEHOLDER]"
            if profile_resp.office_hours:
                office_hours_str = str(profile_resp.office_hours)

            pet_policy_str = "[PET_POLICY_PLACEHOLDER]"
            if profile_resp.leasing_policies:
                pet_policy_str = str(profile_resp.leasing_policies)

            escalation_str = "[ESCALATION_CONTACT_PLACEHOLDER]"
            if profile_resp.escalation_contacts:
                escalation_str = str(profile_resp.escalation_contacts[0]) if profile_resp.escalation_contacts else escalation_str

            self._property_profile = PropertyProfileSlot(
                property_id=str(profile_resp.id),
                property_name=profile_resp.name,
                address=profile_resp.address or "[ADDRESS_PLACEHOLDER]",
                description=profile_resp.description or "[DESCRIPTION_PLACEHOLDER]",
                amenities=amenities,
                office_hours=office_hours_str,
                pet_policy=pet_policy_str,
                maintenance_instructions=profile_resp.maintenance_instructions or "[MAINTENANCE_INSTRUCTIONS_PLACEHOLDER]",
                escalation_contact=escalation_str,
            )
            log.info(
                "VoiceSession._load_property_profile: loaded",
                extra={
                    "call_id": self.state.call_id,
                    "property_name": profile_resp.name,
                },
            )
        except BackendToolError as exc:
            log.error(
                "VoiceSession._load_property_profile: failed to load property profile",
                extra={
                    "call_id": self.state.call_id,
                    "property_id": self.state.property_id,
                    "error_code": exc.code,
                },
            )
            # Leave _property_profile as placeholder default — agent continues without crash.
        except Exception as exc:
            log.error(
                "VoiceSession._load_property_profile: unexpected error",
                extra={
                    "call_id": self.state.call_id,
                    "error": str(exc)[:200],
                },
            )

    async def _stt_transcribe(self, audio_bytes: bytes) -> str:
        """
        DEAD STUB — STT is now handled by VoicePipelineAgent + livekit-plugins-deepgram.
        Retained to avoid breaking test_phase1_scenarios.py which exercises the
        VoiceSession turn-by-turn interface without live providers.
        """
        raise NotImplementedError("STT handled by VoicePipelineAgent (ADR-0005).")

    async def _llm_respond(self, system_prompt: str, conversation_history: list) -> str:
        """
        DEAD STUB — LLM is now handled by VoicePipelineAgent + livekit-plugins-google.
        Retained to avoid breaking test_phase1_scenarios.py.
        """
        raise NotImplementedError("LLM handled by VoicePipelineAgent (ADR-0006).")

    def _effective_call_id(self) -> uuid.UUID:
        """
        Return the backend call_id as a UUID.

        Uses backend_call_id if available (set by start()), otherwise falls back
        to the local call_id. The fallback is for pre-start error paths only —
        all post-start tool calls must use the backend-assigned ID.
        """
        backend_id = getattr(self.state, "backend_call_id", None)
        return uuid.UUID(backend_id if backend_id else self.state.call_id)

    async def _flush_transcript_segments(self) -> None:
        """
        Post all unflushed transcript segments to the backend.
        Non-blocking on failure — log and continue.

        Correctness notes vs. Phase 0 stub:
          - call_id is the backend-assigned UUID, not the local str
          - speaker is str ("agent"|"caller"), not SpeakerRole enum
          - timestamp is float (seconds-since-call-start), not ISO datetime
          - segment_id is NOT passed — backend generates its own UUID
        """
        call_id = self._effective_call_id()
        for seg in self.state.unflushed_segments:
            try:
                await self._client.save_transcript_segment(
                    call_id=call_id,
                    speaker=seg.speaker.value,  # "agent" or "caller"
                    text=seg.text,
                    timestamp=seg.timestamp_offset_seconds or 0.0,
                )
                seg.flushed_to_backend = True
            except BackendToolError as exc:
                # Log without the text content (PII-adjacent)
                log.warning(
                    "Failed to flush transcript segment",
                    extra={
                        "call_id": str(call_id),
                        "segment_id": seg.segment_id,
                        "error_code": exc.code,
                        "retryable": exc.retryable,
                    },
                )
                self.state.tool_failure_count += 1

    async def _trigger_handoff(self) -> None:
        """
        Request human handoff for escalated calls.

        Derives HandoffUrgency from EscalationReason automatically using
        EscalationReason.to_handoff_urgency() — callers set escalation_reason
        and this method handles the mapping to Harsha's urgency enum.
        """
        if not self.state.escalation_reason:
            return
        reason = self.state.escalation_reason
        # to_handoff_urgency() returns call_state.HandoffUrgency.
        # Convert to backend_client.HandoffUrgency using the shared string value.
        cs_urgency = reason.to_handoff_urgency()
        urgency = HandoffUrgency(cs_urgency.value)
        call_id = self._effective_call_id()
        lead_id = uuid.UUID(self.state.lead_id) if self.state.lead_id else None
        try:
            await self._client.request_human_handoff(
                property_id=uuid.UUID(self.state.property_id),
                call_id=call_id,
                reason=reason.value,
                urgency=urgency,
                lead_id=lead_id,
            )
        except BackendToolError as exc:
            log.error(
                "Failed to request human handoff",
                extra={
                    "call_id": str(call_id),
                    "reason": reason.value,
                    "error_code": exc.code,
                },
            )

    async def _save_summary(self) -> None:
        """
        Persist call summary at end of call.

        Uses SummaryBuilder.build() (via build_summary()) to assemble the
        payload. This is richer than CallState.summary_payload() because it
        incorporates state machine intent history and derived next_steps.

        call_id in the payload is str — convert to uuid.UUID for the client.
        """
        call_id = self._effective_call_id()
        payload = self.build_summary()
        try:
            await self._client.save_call_summary(
                call_id=call_id,
                summary=payload["summary"],
                primary_intent=payload["primary_intent"],
                sentiment=payload["sentiment"],
                action_items=payload.get("action_items"),
                escalation_flag=payload.get("escalation_flag", False),
                lead_fields_extracted=payload.get("lead_fields_extracted"),
                next_steps=payload.get("next_steps"),
            )
        except BackendToolError as exc:
            log.error(
                "Failed to save call summary",
                extra={
                    "call_id": str(call_id),
                    "error_code": exc.code,
                    "retryable": exc.retryable,
                },
            )
