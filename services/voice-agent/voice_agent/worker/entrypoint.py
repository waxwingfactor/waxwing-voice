"""
LiveKit Agents worker entrypoint for Waxwing Voice.

Architecture (ADR-0004): Twilio → LiveKit bridge.
  - Harsha's backend accepts Twilio Media Streams, resamples mu-law → 16kHz PCM,
    and publishes the caller audio to a per-call LiveKit room.
  - This worker is registered as a LiveKit Agents job handler. LiveKit dispatches
    one job per call (per room). The entrypoint runs for the lifetime of the call.
  - Room metadata (set by Harsha at room creation) carries call context:
      property_id, company_id, twilio_call_sid, caller_phone, livekit_room_id

Per-turn loop:
    1. LiveKit AudioStream delivers 16kHz PCM frames from the caller.
    2. AudioStreamAdapter converts frames to AsyncIterator[bytes] for the STT adapter.
    3. WhisperSTTAdapter accumulates audio → POSTs to Whisper → yields TranscriptionEvent.
    4. VoiceSession.handle_caller_turn(caller_text) runs the conversation pipeline:
         - EscalationDetector
         - LeadCaptureStateMachine
         - RetrievalCoordinator (RAG)
         - TourBookingCoordinator / FollowUpEmailCoordinator
    5. System prompt built from property context + RAG chunks.
    6. GeminiLLMAdapter.respond_streaming() generates the agent response.
    7. ElevenLabsTTSAdapter.synthesize_streaming() synthesises audio.
    8. AudioSinkAdapter pushes PCM to the LiveKit room → Harsha's bridge → Twilio → caller.

Barge-in (caller interrupts agent):
    LiveKit VAD fires participant speech event while TTS is playing.
    Worker calls tts.cancel() and stt.cancel() (if STT is mid-buffer).
    The per-turn loop restarts with the new caller audio.

Silence handling:
    If no STT event arrives within SILENCE_TIMEOUT_SECONDS after the last event,
    the worker prompts "Are you still there?" If silence continues, the call ends.

Lifecycle events emitted:
    - call_started: on session.start() success
    - tool_called, knowledge_retrieved, lead_captured, tour_booked, email_sent:
      emitted by the coordinators inside VoiceSession
    - tool_failed: on STT/LLM/TTS provider errors
    - call_ended: on clean hangup
    - call_ended (partial): on unexpected disconnect

Import guard:
    livekit.agents is imported with a try/except so that unit tests can import
    this module without the package installed. The worker cannot function without
    livekit-agents at runtime, but all test scenarios use mock providers and
    bypass the LiveKit framework entirely.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

log = logging.getLogger("voice_agent.worker.entrypoint")

# Guard against missing livekit-agents during test runs.
try:
    from livekit.agents import JobContext, WorkerOptions, cli
    from livekit.agents.pipeline import VoicePipelineAgent
    _LIVEKIT_AVAILABLE = True
except ImportError:
    JobContext = None  # type: ignore[assignment,misc]
    WorkerOptions = None  # type: ignore[assignment,misc]
    cli = None  # type: ignore[assignment]
    _LIVEKIT_AVAILABLE = False

from voice_agent.agent.session import VoiceSession
from voice_agent.config import get_settings
from voice_agent.tools.backend_client import BackendClient, BackendToolError, CallEventType
from voice_agent.worker.audio_io import AudioSinkAdapter, AudioStreamAdapter, create_audio_source

# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

# How long to wait for the first caller utterance before prompting.
_INITIAL_GREETING_TIMEOUT = 2.0  # seconds

# Greeting spoken when the caller connects.
_GREETING_TEXT = (
    "Hello, thank you for calling. "
    "I'm your leasing assistant. How can I help you today?"
)

# Silence prompt when caller goes quiet.
_SILENCE_PROMPT_TEXT = "I'm still here. Did you have a question?"

# Final silence close — spoken before ending a silent call.
_SILENCE_CLOSE_TEXT = (
    "I haven't heard anything for a while. "
    "Please call back when you're ready. Have a great day!"
)

# Maximum consecutive silence prompts before ending the call.
_MAX_SILENCE_PROMPTS = 2


# -------------------------------------------------------------------------
# Room metadata parser
# -------------------------------------------------------------------------


def _parse_room_metadata(metadata_str: str) -> dict[str, str]:
    """
    Parse the JSON metadata string set by Harsha's backend on room creation.

    Expected format::

        {
            "property_id":    "<uuid>",
            "company_id":     "<uuid>",
            "twilio_call_sid": "CA...",
            "caller_phone":   "+1...",
            "livekit_room_id": "<room-name>"
        }

    Args:
        metadata_str: Raw metadata string from LiveKit room.

    Returns:
        Parsed dict. Empty dict if metadata is missing or malformed.
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


# -------------------------------------------------------------------------
# Provider factory
# -------------------------------------------------------------------------


def _build_stt_adapter() -> Any:
    """
    Build the configured STT adapter from settings.

    Returns:
        WhisperSTTAdapter or MockSTTAdapter depending on STT_PROVIDER env var.
    """
    settings = get_settings()
    provider = getattr(settings, "stt_provider", "whisper")
    if provider == "mock":
        from voice_agent.providers.stt.mock import MockSTTAdapter
        log.info("Worker: using MockSTTAdapter (STT_PROVIDER=mock)")
        return MockSTTAdapter(scripts=[])
    else:
        from voice_agent.providers.stt.whisper import WhisperSTTAdapter
        api_key = getattr(settings, "whisper_api_key", None)
        if not api_key:
            log.warning(
                "WHISPER_API_KEY not set — falling back to MockSTTAdapter. "
                "Set WHISPER_API_KEY or STT_PROVIDER=mock to suppress."
            )
            from voice_agent.providers.stt.mock import MockSTTAdapter
            return MockSTTAdapter(scripts=[])
        return WhisperSTTAdapter(api_key=api_key)


def _build_llm_adapter() -> Any:
    """
    Build the configured LLM adapter from settings.

    Returns:
        GeminiLLMAdapter or MockLLMAdapter depending on LLM_PROVIDER env var.
    """
    settings = get_settings()
    provider = getattr(settings, "llm_provider", "gemini")
    if provider == "mock":
        from voice_agent.providers.llm.mock import MockLLMAdapter
        log.info("Worker: using MockLLMAdapter (LLM_PROVIDER=mock)")
        return MockLLMAdapter(responses=[])
    else:
        from voice_agent.providers.llm.gemini import GeminiLLMAdapter
        api_key = settings.gemini_api_key
        if not api_key:
            log.warning(
                "GEMINI_API_KEY not set — falling back to MockLLMAdapter. "
                "Set GEMINI_API_KEY or LLM_PROVIDER=mock to suppress."
            )
            from voice_agent.providers.llm.mock import MockLLMAdapter
            return MockLLMAdapter(responses=[])
        return GeminiLLMAdapter(api_key=api_key, model=settings.gemini_model)


# -------------------------------------------------------------------------
# Per-turn pipeline
# -------------------------------------------------------------------------


async def _run_agent_turn(
    session: VoiceSession,
    caller_text: str,
    llm: Any,
    tts: Any,
    audio_sink: AudioSinkAdapter,
    client: BackendClient,
) -> bool:
    """
    Run one complete agent turn: LLM → TTS → audio push.

    Args:
        session:     VoiceSession with current call state.
        caller_text: Transcribed caller utterance.
        llm:         LLMAdapter instance.
        tts:         TTSAdapter instance.
        audio_sink:  AudioSinkAdapter to push TTS output.
        client:      BackendClient for lifecycle events.

    Returns:
        True if the turn completed normally.
        False if the call should end (escalation or critical failure).
    """
    # 1. Run conversation pipeline
    try:
        turn_result = await session.handle_caller_turn(caller_text)
    except Exception as exc:
        log.error(
            "Worker: handle_caller_turn failed",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )
        return False

    # 2. Check escalation — if triggered, end call after TTS
    escalated = turn_result.get("escalated", False)

    # 3. Build response text
    # In Phase 1, we use LLM to generate the response. Safety guardrails are
    # already in the system prompt and in the EscalationDetector. If escalated,
    # the LLM is still called to generate a graceful handoff message.
    from voice_agent.prompts.system_prompt import build_system_prompt

    # Build system prompt with RAG context if available
    system_prompt = build_system_prompt(
        knowledge=session._last_knowledge_slot,
        phase=session.state.phase.value,
    )

    # Build conversation history from call state transcript
    history = _build_history_from_state(session)

    # 4. Generate LLM response
    response_text = ""
    log.info(
        "PIPELINE[4/7] LLM starting: call_id=%s history_turns=%d",
        session.state.call_id, len(history),
    )
    try:
        async for token in llm.respond_streaming(system_prompt, history):
            response_text += token
    except Exception as exc:
        log.error(
            "Worker: LLM generation failed",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )
        # Emit tool_failed event (best-effort)
        _emit_event_safe(
            client, session,
            CallEventType.tool_failed,
            {"tool": "llm", "error": str(exc)[:200]},
        )
        # Fall back to a safe canned response
        response_text = "I'm sorry, I'm having trouble processing that. Let me connect you with someone who can help."
        escalated = True

    if not response_text.strip():
        response_text = "I didn't catch that. Could you repeat your question?"

    log.info(
        "PIPELINE[4/7] LLM complete: call_id=%s response_chars=%d text_preview=%.80r",
        session.state.call_id, len(response_text), response_text,
    )

    # 5. Record agent response in call state transcript
    from voice_agent.state.call_state import SpeakerRole
    session.state.add_segment(
        speaker=SpeakerRole.AGENT,
        text=response_text,
    )

    # 6. Stream TTS audio to room
    log.info(
        "PIPELINE[5/7] TTS starting: call_id=%s text_chars=%d",
        session.state.call_id, len(response_text),
    )
    tts_chunks = 0
    tts_bytes = 0
    try:
        async for chunk in tts.synthesize_streaming(response_text):
            await audio_sink.push(chunk)
            tts_chunks += 1
            tts_bytes += len(chunk)
    except Exception as exc:
        log.error(
            "Worker: TTS synthesis failed",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )
        _emit_event_safe(
            client, session,
            CallEventType.tool_failed,
            {"tool": "tts", "error": str(exc)[:200]},
        )
        session.state.tool_failure_count += 1
    else:
        log.info(
            "PIPELINE[5/7] TTS complete: call_id=%s chunks=%d bytes=%d",
            session.state.call_id, tts_chunks, tts_bytes,
        )

    return not escalated


def _build_history_from_state(session: VoiceSession) -> list[dict]:
    """
    Build conversation history from the call state transcript segments.

    Returns a list of {role, content} dicts suitable for the LLM adapter.
    Keeps the last N turns to avoid unbounded context growth.
    """
    MAX_HISTORY_TURNS = 10  # ~5 caller + 5 agent turns
    from voice_agent.state.call_state import SpeakerRole

    history = []
    for seg in session.state.transcript[-MAX_HISTORY_TURNS:]:
        role = "user" if seg.speaker == SpeakerRole.CALLER else "assistant"
        history.append({"role": role, "content": seg.text})
    return history


def _emit_event_safe(
    client: BackendClient,
    session: VoiceSession,
    event_type: CallEventType,
    payload: dict,
) -> None:
    """
    Schedule a non-blocking backend event emission.

    Uses asyncio.create_task so the event fire-and-forget doesn't block the
    per-turn loop. The task is logged if it fails.
    """
    if not session.state.backend_call_id:
        return

    async def _emit() -> None:
        try:
            await client.create_call_event(
                call_id=uuid.UUID(session.state.backend_call_id),  # type: ignore[arg-type]
                event_type=event_type,
                payload=payload,
            )
        except BackendToolError as exc:
            log.warning(
                "Worker: non-blocking event emission failed",
                extra={
                    "event_type": event_type.value,
                    "error_code": exc.code,
                },
            )

    asyncio.create_task(_emit())


# -------------------------------------------------------------------------
# Main entrypoint function
# -------------------------------------------------------------------------


async def entrypoint(ctx: Any) -> None:
    """
    LiveKit Agents job entrypoint — called once per inbound call.

    This function runs for the entire duration of one phone call. It:
    1. Parses room metadata to get call context.
    2. Builds provider adapters (STT, LLM, TTS).
    3. Calls VoiceSession.start() to create the backend call record.
    4. Speaks the greeting.
    5. Runs the per-turn loop until the caller hangs up or escalation triggers.
    6. On disconnect: builds summary, saves to backend, emits call_ended.

    Args:
        ctx: LiveKit JobContext (or a mock with the same interface for tests).
             Expected attributes:
               ctx.room.name         — LiveKit room name (= Twilio call SID by convention)
               ctx.room.metadata     — JSON string set by Harsha's backend
               ctx.room.on(...)      — event subscription method
    """
    log.info("Worker entrypoint called", extra={"room": getattr(ctx.room, "name", "unknown")})

    settings = get_settings()

    # --- 1. Parse room metadata ---
    metadata = _parse_room_metadata(getattr(ctx.room, "metadata", "") or "")
    property_id = metadata.get("property_id") or settings.default_property_id
    twilio_call_sid = metadata.get("twilio_call_sid") or getattr(ctx.room, "name", None)
    livekit_room_id = metadata.get("livekit_room_id") or getattr(ctx.room, "name", None)
    caller_phone = metadata.get("caller_phone")

    if not property_id:
        log.error(
            "Worker: property_id missing from room metadata and DEFAULT_PROPERTY_ID not set. "
            "Cannot start call.",
            extra={"room": getattr(ctx.room, "name", "unknown")},
        )
        return

    # --- 2. Build provider adapters ---
    stt = _build_stt_adapter()
    llm = _build_llm_adapter()

    # TTS adapter is handled by VoiceSession's existing logic (falls back to mock)
    # We build it explicitly here so we can cancel() on barge-in.
    if settings.tts_provider == "mock":
        from voice_agent.providers.tts.mock import MockTTSAdapter
        tts = MockTTSAdapter()
    else:
        from voice_agent.providers.tts.elevenlabs import ElevenLabsTTSAdapter
        if settings.elevenlabs_api_key:
            tts = ElevenLabsTTSAdapter(
                api_key=settings.elevenlabs_api_key,
                voice_id=settings.elevenlabs_voice_id,
            )
        else:
            from voice_agent.providers.tts.mock import MockTTSAdapter
            tts = MockTTSAdapter()
            log.warning("Worker: ELEVENLABS_API_KEY not set, using MockTTSAdapter")

    # --- 3. Build BackendClient and VoiceSession ---
    client = BackendClient(
        base_url=settings.backend_api_url,
        jwt_token=settings.voice_agent_jwt,
        timeout_seconds=settings.backend_api_timeout_seconds,
    )

    session = VoiceSession(
        property_id=property_id,
        jwt_token=settings.voice_agent_jwt,
        backend_client=client,
        tts_adapter=tts,
        twilio_call_sid=twilio_call_sid,
        livekit_room_id=livekit_room_id,
        caller_phone_number=caller_phone,
    )

    # --- 4. Create audio sink for agent speech ---
    audio_source, audio_track = await create_audio_source()
    audio_sink = AudioSinkAdapter(audio_source)

    # Publish agent audio track to room (no-op if livekit not available)
    if _LIVEKIT_AVAILABLE and audio_track and hasattr(ctx.room, "local_participant"):
        try:
            await ctx.room.local_participant.publish_track(audio_track)
            log.info("Worker: agent audio track published")
        except Exception as exc:
            log.error(
                "Worker: failed to publish agent audio track",
                extra={"error": str(exc)[:200]},
            )

    # --- 5. Start session (creates backend call record, emits call_started) ---
    try:
        await session.start()
    except BackendToolError as exc:
        log.error(
            "Worker: session.start() failed — cannot proceed",
            extra={"error_code": exc.code, "retryable": exc.retryable},
        )
        return

    # --- 6. Speak greeting ---
    try:
        async for chunk in tts.synthesize_streaming(_GREETING_TEXT):
            await audio_sink.push(chunk)
        from voice_agent.state.call_state import SpeakerRole
        session.state.add_segment(SpeakerRole.AGENT, _GREETING_TEXT)
    except Exception as exc:
        log.warning(
            "Worker: greeting TTS failed",
            extra={"error": str(exc)[:200]},
        )
        session.state.tool_failure_count += 1

    # --- 7. Set up barge-in via room event subscription ---
    # LiveKit fires a participant speech event when the caller starts speaking.
    # We track whether TTS is currently active so we can cancel it.
    _tts_active = False

    async def _on_caller_speech_started(*args: Any) -> None:
        nonlocal _tts_active
        if _tts_active:
            log.debug("Worker: barge-in detected — cancelling TTS")
            await tts.cancel()
            _tts_active = False

    if _LIVEKIT_AVAILABLE and hasattr(ctx.room, "on"):
        ctx.room.on("participant_speech_started", _on_caller_speech_started)

    # --- 8. Per-turn loop ---
    silence_prompt_count = 0
    call_ended_reason = "normal"

    # Wire up disconnect handler
    _disconnected = asyncio.Event()

    async def _on_participant_disconnected(participant: Any) -> None:
        # Only care about the caller (remote participant)
        if _LIVEKIT_AVAILABLE and hasattr(participant, "identity"):
            if getattr(participant, "identity", "") != "agent":
                log.info(
                    "Worker: caller disconnected",
                    extra={"participant": str(participant)[:100]},
                )
                _disconnected.set()

    if _LIVEKIT_AVAILABLE and hasattr(ctx.room, "on"):
        ctx.room.on("participant_disconnected", _on_participant_disconnected)

    try:
        # Subscribe to caller's audio stream via LiveKit track_subscribed event.
        # The Twilio bridge (Harsha's backend) joins as "twilio-bridge" and publishes
        # a RemoteAudioTrack containing 16kHz PCM frames. We queue arriving tracks
        # so the async loop can await them without blocking the event callback.
        caller_audio_stream = None
        _track_queue: asyncio.Queue = asyncio.Queue()

        _lk_rtc: Any = None
        if _LIVEKIT_AVAILABLE and hasattr(ctx.room, "on"):
            try:
                from livekit import rtc as lk_rtc  # type: ignore[import-not-found]
                _lk_rtc = lk_rtc

                @ctx.room.on("track_subscribed")
                def _on_track_subscribed(track: Any, _: Any, participant: Any) -> None:
                    if isinstance(track, lk_rtc.RemoteAudioTrack):
                        log.info(
                            "Worker: caller audio track subscribed",
                            extra={"participant": str(getattr(participant, "identity", ""))[:50]},
                        )
                        _track_queue.put_nowait(track)

            except ImportError:
                log.warning("Worker: livekit.rtc not available — audio subscription disabled")

        # Main loop: process caller utterances
        while not _disconnected.is_set():
            if caller_audio_stream is None:
                if not _LIVEKIT_AVAILABLE or _lk_rtc is None:
                    log.debug("Worker: LiveKit not available — per-turn loop idle")
                    break
                # Wait for the bridge to publish the caller's audio track
                try:
                    track = await asyncio.wait_for(_track_queue.get(), timeout=2.0)
                    caller_audio_stream = _lk_rtc.AudioStream(track)
                    log.info("Worker: caller audio stream ready — starting STT loop")
                except asyncio.TimeoutError:
                    continue  # track not yet arrived; keep waiting

            # STT: transcribe the caller's audio stream
            log.info("PIPELINE[3/7] STT starting — collecting caller audio frames")
            stream_adapter = AudioStreamAdapter(caller_audio_stream)
            caller_text = ""
            last_partial = ""

            async for event in stt.transcribe_streaming(stream_adapter.as_async_iter()):
                if not event.is_final:
                    # Partial: check for barge-in if confidence is high enough
                    last_partial = event.text
                    if event.confidence and event.confidence > 0.7 and _tts_active:
                        log.debug("Worker: high-confidence partial → barge-in")
                        await tts.cancel()
                        _tts_active = False
                    continue
                caller_text = event.text
                log.info(
                    "PIPELINE[3/7] STT result: text=%.120r confidence=%s",
                    caller_text, getattr(event, "confidence", None),
                )
                break  # final event received; proceed to LLM turn

            if not caller_text.strip():
                # Silence
                silence_prompt_count += 1
                if silence_prompt_count > _MAX_SILENCE_PROMPTS:
                    log.info("Worker: max silence prompts reached, ending call")
                    try:
                        async for chunk in tts.synthesize_streaming(_SILENCE_CLOSE_TEXT):
                            await audio_sink.push(chunk)
                    except Exception:
                        pass
                    call_ended_reason = "silence"
                    break
                else:
                    try:
                        async for chunk in tts.synthesize_streaming(_SILENCE_PROMPT_TEXT):
                            await audio_sink.push(chunk)
                    except Exception:
                        pass
                    continue
            else:
                silence_prompt_count = 0  # reset on successful utterance

            # Record caller utterance in transcript
            from voice_agent.state.call_state import SpeakerRole
            session.state.add_segment(SpeakerRole.CALLER, caller_text)

            # Run agent turn: LLM → TTS
            _tts_active = True
            call_continues = await _run_agent_turn(
                session=session,
                caller_text=caller_text,
                llm=llm,
                tts=tts,
                audio_sink=audio_sink,
                client=client,
            )
            _tts_active = False

            if not call_continues:
                call_ended_reason = "escalation"
                break

    except asyncio.CancelledError:
        call_ended_reason = "cancelled"
        log.info("Worker: per-turn loop cancelled")
    except Exception as exc:
        call_ended_reason = "error"
        log.error(
            "Worker: unhandled exception in per-turn loop",
            extra={"error": str(exc)[:200]},
        )
        _emit_event_safe(
            client, session,
            CallEventType.tool_failed,
            {"tool": "worker_loop", "error": str(exc)[:200]},
        )

    # --- 9. End session: flush transcript, save summary, emit call_ended ---
    await _end_session(session, client, call_ended_reason)


async def _end_session(
    session: VoiceSession,
    client: BackendClient,
    reason: str,
) -> None:
    """
    Clean up at end of call: flush transcript, save summary, emit call_ended.

    This runs regardless of whether the call ended normally, via escalation,
    via silence, or via unexpected disconnect. "Partial" calls (mid-stream
    disconnect) are marked with reason="early_disconnect".
    """
    log.info(
        "Worker: ending session",
        extra={"call_id": session.state.call_id, "reason": reason},
    )

    try:
        # VoiceSession.end() flushes transcript, triggers handoff if escalated,
        # saves summary via SummaryBuilder, emits call_ended.
        await session.end(reason=reason)
    except Exception as exc:
        log.error(
            "Worker: session.end() raised an exception",
            extra={"call_id": session.state.call_id, "error": str(exc)[:200]},
        )

    # Emit call_ended event (best-effort — session.end() may have already done this,
    # but we emit here as a safety net if session.end() partially failed).
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

    log.info(
        "Worker: session ended",
        extra={"call_id": session.state.call_id, "reason": reason},
    )


# -------------------------------------------------------------------------
# Worker registration
# -------------------------------------------------------------------------


def create_worker_options() -> Any:
    """
    Build the LiveKit WorkerOptions for this worker.

    Called from __main__.py to register the entrypoint with the Agents framework.
    Returns None if livekit-agents is not installed (test/offline mode).
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
