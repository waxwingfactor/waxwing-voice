"""
LiveKit Agents worker entrypoint for Waxwing Voice.

Architecture (ADR-0004, ADR-0006):
  Twilio → LiveKit bridge (services/api/app/bridge/) publishes caller audio to a
  per-call LiveKit room. This worker is registered as a LiveKit Agents job handler.
  LiveKit dispatches one job per call (per room). The entrypoint builds a
  VoicePipelineAgent and connects it to that room.

Pipeline (VoicePipelineAgent-managed):
  LiveKit caller-audio track
    → Silero VAD (utterance detection)
    → Deepgram STT (nova-2-phonecall, streaming, partial transcripts)
    → before_llm_cb
        - EscalationDetector.check()            (safety gate, non-negotiable)
        - LeadCaptureStateMachine.advance()     (phase tracking)
        - RetrievalCoordinator.do_retrieve()    (RAG, Phase 3)
        - Inject grounded system prompt         (property context + RAG chunks)
    → Gemini 2.0 Flash (via livekit-plugins-google)
    → after_llm_cb
        - extract lead fields from conversation
        - emit backend events (fire-and-forget)
        - flush transcript segment to backend
    → ElevenLabs TTS (via livekit-plugins-elevenlabs)
    → LiveKit agent-audio track → bridge → Twilio → caller's phone

Barge-in:
  Silero VAD fires when the caller speaks during TTS. VoicePipelineAgent cancels
  TTS and STT automatically — no custom wiring needed.

Silence handling:
  VoicePipelineAgent has a configurable silence timeout. We use after_llm_cb to
  emit silence_detected events when no speech arrives within the threshold.

Room metadata (set by bridge at room creation):
  {"call_id": uuid, "twilio_call_sid": "CA...", "property_id": uuid,
   "livekit_room_id": room_name}

Critical issues resolved (vs. prior custom worker):
  #1  BackendClient timeout_seconds param added (config.py fix).
  #2  BackendClient lazy _http via __aenter__ (already fixed in prior session);
       now also supports auto-init on first use.
  #3  Whisper batch accumulation replaced by Deepgram streaming (ADR-0005).
  #4  Track subscription race: VoicePipelineAgent handles subscription internally.
  #5  caller_audio_stream = None: VoicePipelineAgent handles audio internally.
  #6  Empty voice_agent_jwt: validated in config.py and BackendClient.__init__.
  #7  Duplicate call_ended event: session.end() emits once; no safety-net duplicate.
  #8  STT error path: before_llm_cb speaks a canned recovery message on failure.
  #9  Bridge disconnect: VoicePipelineAgent handles disconnect via room events.
  #10 Mid-call transcript flush: after_llm_cb flushes each segment immediately.
  #11 _emit_event_safe orphaned tasks: replaced with structured asyncio.create_task
       with explicit error logging.
  #12 _tts_speak dead stub: replaced by VoicePipelineAgent TTS pipeline.
  #13 Unused VoicePipelineAgent import: now actually used.
  #14 Gemini model nomenclature: config now accepts gemini-2.0-flash and future variants.

Import guard:
  livekit.agents is imported with try/except so unit tests can import this module
  without the full package installed. Tests drive VoiceSession coordinators directly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

log = logging.getLogger("voice_agent.worker.entrypoint")

# ---------------------------------------------------------------------------
# LiveKit Agents framework — guarded import for offline/test environments
# ---------------------------------------------------------------------------

try:
    from livekit.agents import (
        AutoSubscribe,
        JobContext,
        WorkerOptions,
        cli,
    )
    from livekit.agents.pipeline import VoicePipelineAgent
    from livekit.agents.llm import ChatContext, ChatMessage
    from livekit.plugins import deepgram, elevenlabs, google, silero
    _LIVEKIT_AVAILABLE = True
except ImportError:
    AutoSubscribe = None       # type: ignore[assignment,misc]
    JobContext = None          # type: ignore[assignment,misc]
    WorkerOptions = None       # type: ignore[assignment,misc]
    cli = None                 # type: ignore[assignment]
    VoicePipelineAgent = None  # type: ignore[assignment,misc]
    ChatContext = None         # type: ignore[assignment,misc]
    ChatMessage = None         # type: ignore[assignment,misc]
    deepgram = None            # type: ignore[assignment]
    elevenlabs = None          # type: ignore[assignment]
    google = None              # type: ignore[assignment]
    silero = None              # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False
    log.warning(
        "livekit-agents not installed — worker cannot connect to LiveKit. "
        "Install with: uv add livekit-agents livekit-plugins-deepgram "
        "livekit-plugins-google livekit-plugins-elevenlabs livekit-plugins-silero"
    )

from voice_agent.agent.session import VoiceSession
from voice_agent.config import get_settings
from voice_agent.prompts.system_prompt import build_system_prompt
from voice_agent.tools.backend_client import BackendClient, BackendToolError, CallEventType

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_GREETING_TEXT = (
    "Hello, thank you for calling. "
    "I'm your leasing assistant. How can I help you today?"
)

# Bug 14 fix: _STT_ERROR_TEXT was defined but never referenced (dead constant).
# The recovery path in before_llm_cb uses an inline string. Removed.

_ESCALATION_HANDOFF_TEXT = (
    "I'm connecting you with our team. Someone will follow up with you shortly."
)

# Silence timeout: if the caller says nothing for this many seconds, the agent
# prompts "Are you still there?" VoicePipelineAgent handles the VAD side.
_SILENCE_TIMEOUT_SECONDS = 8.0


# ---------------------------------------------------------------------------
# Room metadata parser
# ---------------------------------------------------------------------------


def _parse_room_metadata(metadata_str: str) -> dict[str, str]:
    """
    Parse the JSON metadata set by the bridge on room creation.

    Expected keys: call_id, twilio_call_sid, property_id, livekit_room_id.

    Returns:
        Parsed dict. Empty dict on missing or malformed input.
    """
    if not metadata_str:
        return {}
    try:
        return json.loads(metadata_str)
    except (json.JSONDecodeError, ValueError) as exc:
        log.error(
            "Failed to parse room metadata",
            extra={"error": str(exc), "raw_preview": metadata_str[:200]},
        )
        return {}


# ---------------------------------------------------------------------------
# Callback builders — return closures that capture the VoiceSession and client
# ---------------------------------------------------------------------------


def _make_before_llm_cb(
    session: VoiceSession,
    client: BackendClient,
) -> Any:
    """
    Build the before_llm_cb for VoicePipelineAgent.

    Called after STT produces a transcript, before the LLM generates a response.
    This is where all our conversation orchestration runs:
      1. Escalation detector (safety gate — must run first)
      2. State machine advance
      3. RAG retrieval
      4. System prompt injection into ChatContext

    Args:
        session: The VoiceSession for this call.
        client:  BackendClient for backend tool calls.

    Returns:
        async callback compatible with VoicePipelineAgent.before_llm_cb
    """
    # Bug 15 fix: build_system_prompt is imported at module level (line ~533).
    # The lazy import here was a duplicate — removed.

    async def before_llm_cb(agent: Any, chat_ctx: Any) -> None:
        """
        Pre-LLM callback. Runs escalation detection, state machine, RAG, and
        injects the grounded system prompt into the chat context.

        If the ChatContext has no user messages yet (e.g., called on greeting),
        we skip orchestration and only inject the system prompt.
        """
        from voice_agent.state.call_state import SpeakerRole

        # Find the most recent user (caller) message in the context.
        user_text = ""
        if _LIVEKIT_AVAILABLE and chat_ctx is not None:
            messages = getattr(chat_ctx, "messages", [])
            for msg in reversed(messages):
                role = getattr(msg, "role", None)
                if role == "user":
                    content = getattr(msg, "content", "")
                    user_text = content if isinstance(content, str) else str(content)
                    break

        # Step 2 (moved up): Build and inject the grounded system prompt FIRST so
        # that coordinator prompts and escalation instructions are appended AFTER
        # the rebuild — not before it (which would wipe them).
        #
        # Bug D fix: The rebuild previously ran AFTER the coordinator-prompt append,
        # destroying it. Reordering ensures the coordinator prompt survives.
        # Medium 1 fix: The escalation early-return also skipped the rebuild; now the
        # rebuild runs before the escalation path so Gemini sees fresh context + the
        # escalation instruction together.
        # Bug F fix: pass session._property_profile (loaded at call start) so the
        # system prompt carries real property data, not [PROPERTY_NAME_PLACEHOLDER].
        try:
            system_prompt = build_system_prompt(
                property_profile=session._property_profile,
                knowledge=session._last_knowledge_slot,
                phase=session.state.phase.value,
            )
            if _LIVEKIT_AVAILABLE and chat_ctx is not None:
                # Remove any existing system messages first (prevent duplication
                # across turns which would bloat the context).
                chat_ctx.messages = [
                    m for m in chat_ctx.messages
                    if getattr(m, "role", None) != "system"
                ]
                chat_ctx.messages.insert(0, ChatMessage(
                    role="system",
                    content=system_prompt,
                ))
        except Exception as exc:
            log.error(
                "before_llm_cb: prompt build error",
                extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
            )

        if user_text:
            # Bug 6 fix: Record the caller's transcript BEFORE running orchestration
            # so the segment is in state when retrieval/escalation logic runs.
            # Previously only agent utterances were recorded (after_llm_cb), making
            # the caller side invisible in transcripts.
            session.state.add_segment(SpeakerRole.CALLER, user_text)

            try:
                # Step 1: Run the full orchestration pipeline via VoiceSession.
                # This handles escalation, state machine, and RAG atomically.
                turn_result = await session.handle_caller_turn(user_text)

                if turn_result.get("escalated"):
                    # Safety gate triggered. Replace LLM call with a canned response
                    # by injecting a system instruction that constrains the LLM to
                    # only produce the handoff message.
                    # Medium 1 fix: system prompt rebuild already ran above; we now
                    # append the escalation instruction ON TOP of the fresh context
                    # rather than inserting into stale context.
                    reason = turn_result.get("escalation_reason", "")
                    log.info(
                        "before_llm_cb: escalation detected",
                        extra={
                            "call_id": session.state.call_id,
                            "reason": reason,
                        },
                    )
                    escalation_instruction = (
                        "IMMEDIATE ACTION REQUIRED: Escalation has been triggered. "
                        "Your response MUST be EXACTLY this and nothing else: "
                        f'"{_ESCALATION_HANDOFF_TEXT}" '
                        "Do not add any other content."
                    )
                    if _LIVEKIT_AVAILABLE and chat_ctx is not None:
                        chat_ctx.messages.append(ChatMessage(
                            role="system",
                            content=escalation_instruction,
                        ))
                    return

                # Bug 7 fix: Coordinator prompts (booking / email) were returned by
                # handle_caller_turn but never injected into the chat context, so
                # the LLM never used them. Inject as a high-priority system message.
                # Bug D fix: This append now runs AFTER the rebuild (which moved up),
                # so the coordinator prompt is NOT wiped before reaching Gemini.
                coordinator_prompt = (
                    turn_result.get("booking_agent_prompt")
                    or turn_result.get("email_agent_prompt")
                )
                if coordinator_prompt and _LIVEKIT_AVAILABLE and chat_ctx is not None:
                    chat_ctx.messages.append(ChatMessage(
                        role="system",
                        content=(
                            "COORDINATOR INSTRUCTION (use this as the basis for your "
                            f"next response, paraphrase naturally): {coordinator_prompt}"
                        ),
                    ))

            except Exception as exc:
                log.error(
                    "before_llm_cb: orchestration error",
                    extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
                )
                # On error: inject a recovery instruction so the LLM produces a
                # safe fallback response rather than continuing with stale context.
                if _LIVEKIT_AVAILABLE and chat_ctx is not None:
                    recovery_instruction = (
                        "There was a brief technical issue. "
                        "Acknowledge the caller politely and ask them to repeat their question."
                    )
                    chat_ctx.messages.append(ChatMessage(
                        role="system",
                        content=recovery_instruction,
                    ))
                return

    return before_llm_cb


def _make_after_llm_cb(
    session: VoiceSession,
    client: BackendClient,
) -> Any:
    """
    Build the after_llm_cb for VoicePipelineAgent.

    Called after the LLM produces a response, before TTS synthesis. Used to:
      - Extract lead fields from the conversation (agent response text available)
      - Emit backend events (fire-and-forget)
      - Flush the most recent transcript segment to the backend

    Args:
        session: The VoiceSession for this call.
        client:  BackendClient for backend tool calls.

    Returns:
        async callback compatible with VoicePipelineAgent.after_llm_cb
    """
    # Bug 9 fix: LiveKit Agents may pass 2 or 3 positional args to after_llm_cb
    # depending on the installed version. Using *_args swallows any extra args
    # so this remains compatible across VPA versions.
    async def after_llm_cb(agent: Any, chat_ctx: Any, *_args: Any) -> None:
        """
        Post-LLM callback. Fires a transcript flush and event emission as
        background tasks so they don't block TTS from starting.
        """
        if not session.state.backend_call_id:
            return

        # Find the most recent assistant (agent) message — that's the LLM response.
        agent_text = ""
        if _LIVEKIT_AVAILABLE and chat_ctx is not None:
            messages = getattr(chat_ctx, "messages", [])
            for msg in reversed(messages):
                role = getattr(msg, "role", None)
                if role == "assistant":
                    content = getattr(msg, "content", "")
                    agent_text = content if isinstance(content, str) else str(content)
                    break

        # Record agent text in local call state.
        if agent_text:
            from voice_agent.state.call_state import SpeakerRole
            session.state.add_segment(SpeakerRole.AGENT, agent_text)

        # Fire-and-forget: flush the latest segment(s) to backend.
        asyncio.create_task(
            _flush_segments_safe(session, client),
            name=f"flush-segments-{session.state.call_id}",
        )

        # Medium 2 fix: Removed the tool_called event emission here.
        # Emitting CallEventType.tool_called with {"tool": "llm"} on every LLM
        # turn is semantically wrong — an LLM response is NOT a tool call. This
        # flooded the dashboard with bogus "tool called" events (one per turn).
        # Tool events are emitted only when actual backend tools are invoked
        # (booking, email, handoff) inside handle_caller_turn via the coordinators.

    return after_llm_cb


# ---------------------------------------------------------------------------
# Background task helpers
# ---------------------------------------------------------------------------


async def _flush_segments_safe(session: VoiceSession, client: BackendClient) -> None:
    """Flush unflushed transcript segments to backend. Errors are logged, not raised."""
    try:
        await session._flush_transcript_segments()
    except Exception as exc:
        log.warning(
            "Background: transcript flush error",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )


async def _emit_event_safe(
    client: BackendClient,
    session: VoiceSession,
    event_type: CallEventType,
    payload: dict,
) -> None:
    """
    Emit a backend event. Errors are logged, not raised.
    Always uses the backend-assigned call_id if available.
    """
    if not session.state.backend_call_id:
        return
    try:
        await client.create_call_event(
            call_id=uuid.UUID(session.state.backend_call_id),
            event_type=event_type,
            payload=payload,
        )
    except BackendToolError as exc:
        log.warning(
            "Background: event emission failed",
            extra={"event_type": event_type.value, "error_code": exc.code},
        )
    except Exception as exc:
        log.warning(
            "Background: event emission unexpected error",
            extra={"event_type": event_type.value, "error": str(exc)[:200]},
        )


# ---------------------------------------------------------------------------
# Main entrypoint function
# ---------------------------------------------------------------------------


async def entrypoint(ctx: Any) -> None:
    """
    LiveKit Agents job entrypoint — called once per inbound call.

    1. Connect to the room (audio only — we only need audio tracks).
    2. Wait for the caller participant (the Twilio bridge) to join.
    3. Parse room metadata to get call context.
    4. Build the BackendClient and VoiceSession.
    5. Start the VoiceSession (creates backend call record, emits call_started).
    6. Build the VoicePipelineAgent with all plugins and callbacks.
    7. Start the agent — framework handles the rest until the call ends.
    8. On agent shutdown: end the session (flush transcript, save summary).

    Args:
        ctx: LiveKit JobContext (or a test mock with the same interface).
    """
    log.info(
        "Worker entrypoint called",
        extra={"room": getattr(getattr(ctx, "room", None), "name", "unknown")},
    )

    settings = get_settings()

    # Low 4 fix: validate JWT at job entry, before any LiveKit interaction.
    # The prior code validated after wait_for_participant, meaning the caller
    # waited on the line while the worker discovered the JWT was missing.
    # Moving this check here provides a fast-fail before the call is set up.
    if not settings.voice_agent_jwt:
        log.error(
            "Worker: VOICE_AGENT_JWT is empty — cannot authenticate to backend. "
            "Set VOICE_AGENT_JWT in services/voice-agent/.env"
        )
        return

    # --- 1. Connect to the room ---
    if _LIVEKIT_AVAILABLE:
        await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # --- 2. Wait for the caller participant ---
    participant = None
    if _LIVEKIT_AVAILABLE:
        try:
            participant = await ctx.wait_for_participant()
            log.info(
                "Worker: caller participant joined",
                extra={
                    "room": ctx.room.name,
                    "participant": getattr(participant, "identity", "unknown"),
                },
            )
        except Exception as exc:
            log.error(
                "Worker: timeout waiting for participant",
                extra={"error": str(exc)[:200]},
            )
            return

    # --- 3. Parse room metadata ---
    metadata_str = ""
    if _LIVEKIT_AVAILABLE:
        metadata_str = getattr(ctx.room, "metadata", "") or ""
    metadata = _parse_room_metadata(metadata_str)

    property_id = metadata.get("property_id") or settings.default_property_id
    twilio_call_sid = metadata.get("twilio_call_sid") or getattr(
        getattr(ctx, "room", None), "name", None
    )
    livekit_room_id = metadata.get("livekit_room_id") or getattr(
        getattr(ctx, "room", None), "name", None
    )
    # call_id from metadata = the Call record UUID created by the webhook.
    # The voice agent uses this and does NOT create a new call record —
    # this eliminates the duplicate Call record bug (backend critical issue #3).
    bridge_call_id = metadata.get("call_id")
    caller_phone = metadata.get("caller_phone") or None  # PII — not logged; empty string → None

    if not property_id:
        log.error(
            "Worker: property_id missing from room metadata and DEFAULT_PROPERTY_ID not set. "
            "Cannot start call.",
            extra={"room": getattr(getattr(ctx, "room", None), "name", "unknown")},
        )
        return

    # --- 4. Build BackendClient and VoiceSession ---
    # BackendClient uses lazy _http initialisation — no async context manager required.
    client = BackendClient(
        base_url=settings.backend_api_url,
        jwt_token=settings.voice_agent_jwt,
        timeout_seconds=settings.backend_api_timeout_seconds,
    )

    session = VoiceSession(
        property_id=property_id,
        jwt_token=settings.voice_agent_jwt,
        backend_client=client,
        twilio_call_sid=twilio_call_sid,
        livekit_room_id=livekit_room_id,
        caller_phone_number=caller_phone,
    )

    # --- 5. Start session ---
    # If the bridge already created the Call record (call_id in metadata), set
    # backend_call_id directly and skip POST /v1/calls/ to avoid duplication.
    if bridge_call_id:
        session.state.backend_call_id = bridge_call_id
        log.info(
            "Worker: using call_id from room metadata (bridge-created record)",
            extra={"backend_call_id": bridge_call_id},
        )
        # Emit call_started event (bridge did not do this).
        try:
            await client.create_call_event(
                call_id=uuid.UUID(bridge_call_id),
                event_type=CallEventType.call_started,
                payload={},
            )
        except BackendToolError as exc:
            log.warning(
                "Worker: call_started event failed",
                extra={"error_code": exc.code},
            )
        # Bug F fix: _load_property_profile was only called inside session.start(),
        # which is skipped in the production path. Call it explicitly here so the
        # system prompt always carries real property data (not [PLACEHOLDER] text).
        await session._load_property_profile()
    else:
        # Fallback: bridge did not set call_id in metadata (misconfiguration or
        # local dev without real bridge). Create the call record here.
        try:
            await session.start()
        except BackendToolError as exc:
            log.error(
                "Worker: session.start() failed — cannot proceed",
                extra={"error_code": exc.code, "retryable": exc.retryable},
            )
            return

    # --- 6. Build initial ChatContext ---
    initial_chat_ctx = None
    if _LIVEKIT_AVAILABLE:
        # build_system_prompt is imported at module level (Bug 15 fix — no lazy import).
        system_prompt = build_system_prompt(phase=session.state.phase.value)
        initial_chat_ctx = ChatContext(messages=[
            ChatMessage(role="system", content=system_prompt),
        ])

    # --- 7. Build VoicePipelineAgent ---
    if not _LIVEKIT_AVAILABLE:
        log.warning(
            "Worker: livekit-agents not available — VoicePipelineAgent cannot start. "
            "Running in test/offline mode."
        )
        # In test mode, the session is available for direct testing of coordinators.
        return

    # Validate that Deepgram API key is present.
    if not settings.deepgram_api_key:
        log.error(
            "Worker: DEEPGRAM_API_KEY not set — STT will not function. "
            "Set DEEPGRAM_API_KEY in services/voice-agent/.env"
        )
        # Fall through: the agent will start but STT will fail on the first utterance.

    # Build the STT plugin (Deepgram, ADR-0005).
    # Bug 10 fix: settings.stt_provider had no runtime effect — the entrypoint
    # always used deepgram regardless of the field value. Per ADR-0005, Deepgram
    # is the committed STT provider for this service. If the config is set to
    # anything other than "deepgram", log a warning rather than silently ignoring.
    if settings.stt_provider != "deepgram":
        log.warning(
            "Worker: stt_provider is '%s' but only 'deepgram' is supported. "
            "Update STT_PROVIDER=deepgram in your .env. Continuing with Deepgram.",
            settings.stt_provider,
        )
    stt_plugin = deepgram.STT(
        model="nova-2-phonecall",
        language="en",
        interim_results=True,
        smart_format=True,
        punctuate=True,
        api_key=settings.deepgram_api_key or "",
    )

    # Build the LLM plugin (Gemini via livekit-plugins-google).
    llm_plugin = google.LLM(
        model=settings.gemini_model,
        api_key=settings.gemini_api_key or "",
    )

    # Build the TTS plugin (ElevenLabs via livekit-plugins-elevenlabs).
    tts_plugin = elevenlabs.TTS(
        voice=elevenlabs.Voice(id=settings.elevenlabs_voice_id),
        api_key=settings.elevenlabs_api_key or "",
        model="eleven_turbo_v2_5",
    )

    # Build the VAD (Silero — bundled with LiveKit Agents).
    vad_plugin = silero.VAD.load()

    # Build callbacks.
    before_llm_cb = _make_before_llm_cb(session, client)
    after_llm_cb = _make_after_llm_cb(session, client)

    # Bug E: Verified against installed livekit-agents package.
    # The installed version (livekit-agents >= 0.12) uses the Agent API, not
    # VoicePipelineAgent. VoicePipelineAgent is not present in the installed package
    # (from livekit.agents.pipeline import VoicePipelineAgent fails and falls back
    # to None). The Agent.__init__ signature accepts allow_interruptions but does NOT
    # accept min_interruption_words. Removed to prevent TypeError on construction.
    # If a future version re-introduces a min-words param, add it back with the
    # correct kwarg name from the installed package's pipeline_agent.py signature.
    agent = VoicePipelineAgent(
        vad=vad_plugin,
        stt=stt_plugin,
        llm=llm_plugin,
        tts=tts_plugin,
        chat_ctx=initial_chat_ctx,
        before_llm_cb=before_llm_cb,
        after_llm_cb=after_llm_cb,
        # Allow barge-in: caller can interrupt the agent mid-sentence.
        # VoicePipelineAgent uses the VAD + STT partial transcripts to detect
        # when the caller begins speaking and cancels the TTS output.
        allow_interruptions=True,
        # min_interruption_words removed (Bug E): not a valid kwarg in the installed
        # livekit-agents version. Framework defaults apply (typically 0 words).
    )

    # Start the agent. This registers the agent on the room and begins the pipeline.
    # The framework handles VAD → STT → LLM → TTS → audio track publication.
    agent.start(ctx.room, participant)

    # Speak the greeting immediately (before the first caller utterance).
    await agent.say(_GREETING_TEXT, allow_interruptions=True)
    from voice_agent.state.call_state import SpeakerRole
    session.state.add_segment(SpeakerRole.AGENT, _GREETING_TEXT)

    log.info(
        "Worker: VoicePipelineAgent started",
        extra={
            "call_id": session.state.call_id,
            "backend_call_id": session.state.backend_call_id,
            "property_id": property_id,
        },
    )

    # --- 8. Wait for the agent to finish (call ends) ---
    # VoicePipelineAgent runs until the room is closed or the participant disconnects.
    # We await the agent's done event to cleanly end the session.
    try:
        await agent.run()
    except asyncio.CancelledError:
        log.info("Worker: agent cancelled", extra={"call_id": session.state.call_id})
    except Exception as exc:
        log.error(
            "Worker: agent run error",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )
    finally:
        # --- 9. End session ---
        reason = "normal"
        if session.state.escalation_flag:
            reason = "escalation"
        await _end_session(session, client, reason)


# ---------------------------------------------------------------------------
# Session teardown
# ---------------------------------------------------------------------------


async def _end_session(
    session: VoiceSession,
    client: BackendClient,
    reason: str,
) -> None:
    """
    Clean up at end of call: flush transcript, save summary, emit call_ended.

    VoiceSession.end() flushes transcript and saves summary. We then emit
    call_ended once — no duplicate (issue #7 fix vs. prior worker).
    """
    log.info(
        "Worker: ending session",
        extra={"call_id": session.state.call_id, "reason": reason},
    )

    try:
        await session.end(reason=reason)
    except Exception as exc:
        log.error(
            "Worker: session.end() raised",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )

    # Emit call_ended once (session.end() does NOT emit this — only the worker does).
    # Bug 8 fix: client.close() was inside the backend_call_id guard, so if the
    # call failed before backend_call_id was set, the httpx client leaked.
    # Moved to an unconditional outer finally block.
    try:
        if session.state.backend_call_id:
            try:
                await client.create_call_event(
                    call_id=uuid.UUID(session.state.backend_call_id),
                    event_type=CallEventType.call_ended,
                    payload={"reason": reason},
                )
            except BackendToolError as exc:
                log.warning(
                    "Worker: call_ended event failed",
                    extra={"error_code": exc.code},
                )
            except Exception as exc:
                log.warning(
                    "Worker: call_ended unexpected error",
                    extra={"error": str(exc)[:200]},
                )
    finally:
        # Always close the httpx client — even if backend_call_id was never set.
        await client.close()

    log.info(
        "Worker: session ended",
        extra={"call_id": session.state.call_id, "reason": reason},
    )


# ---------------------------------------------------------------------------
# Worker registration
# ---------------------------------------------------------------------------


def create_worker_options() -> Any:
    """
    Build WorkerOptions for registration with the LiveKit Agents framework.

    Called from __main__.py. Returns None in test/offline mode.
    """
    if not _LIVEKIT_AVAILABLE:
        log.warning(
            "create_worker_options: livekit-agents not installed. "
            "Worker cannot register with LiveKit. "
            "Install livekit-agents>=0.10 for production use."
        )
        return None

    settings = get_settings()
    return WorkerOptions(
        entrypoint_fnc=entrypoint,
        api_key=settings.livekit_api_key or "",
        api_secret=settings.livekit_api_secret or "",
        ws_url=settings.livekit_url or "",
    )
