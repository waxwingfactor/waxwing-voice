# ADR-0006: Adopt LiveKit VoicePipelineAgent Pattern

**Status:** Accepted  
**Date:** 2026-05-09  
**Authors:** Akhil (voice pipeline)  
**Impacted owners:** Akhil (refactor)

---

## Context

The original voice-agent worker used a hand-rolled per-turn loop:

```
AudioStreamAdapter → WhisperSTTAdapter → custom VAD → GeminiLLMAdapter
    → ElevenLabsTTSAdapter → AudioSinkAdapter
```

Each component was a custom adapter class (~100–300 LoC each) that reimplemented
functionality the LiveKit Agents framework already provides:

- Voice Activity Detection (VAD): the framework includes Silero VAD out of the box
- STT batching and streaming: handled by `livekit-plugins-deepgram` / `livekit-plugins-openai`
- LLM streaming: handled by `livekit-plugins-google` / `livekit-plugins-openai`
- TTS streaming: handled by `livekit-plugins-elevenlabs`
- Barge-in cancellation: `VoicePipelineAgent` coordinates this internally
- Silence detection: `VoicePipelineAgent` has configurable silence timeout
- Audio framing and track management: handled by the framework

This divergence from framework idioms caused multiple critical bugs (issues #1–13 in
the prior diagnostics) including broken async context management, an unbounded audio
accumulation loop, and an unreachable TTS stub method.

---

## Decision

**Refactor the worker entrypoint to use `VoicePipelineAgent` as the primary
orchestration primitive.** The conversation coordinators (state machine, escalation,
RAG, booking, email) become callback functions that hook into the pipeline's
`before_llm_cb` and `after_llm_cb` points.

```python
from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from livekit.agents.pipeline import VoicePipelineAgent
from livekit.plugins import deepgram, elevenlabs, google, silero

async def entrypoint(ctx: JobContext):
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    participant = await ctx.wait_for_participant()

    agent = VoicePipelineAgent(
        vad=silero.VAD.load(),
        stt=deepgram.STT(model="nova-2-phonecall", language="en",
                         interim_results=True),
        llm=google.LLM(model="gemini-2.0-flash"),
        tts=elevenlabs.TTS(voice=elevenlabs.Voice(id=settings.elevenlabs_voice_id)),
        chat_ctx=initial_ctx,
        before_llm_cb=_before_llm_cb,
        after_llm_cb=_after_llm_cb,
    )
    agent.start(ctx.room, participant)
```

---

## Rationale

1. **Framework handles the hard parts** — VAD, barge-in cancellation, silence
   timeout, audio track subscription, and partial-transcript coordination are all
   production-tested in the framework. Reimplementing them is a source of bugs.

2. **Clean integration point via callbacks** — `before_llm_cb` is called after STT
   with the transcript and before the LLM. This is the natural place to run the
   escalation detector, state machine advance, and RAG retrieval. `after_llm_cb`
   fires after the LLM response is generated, before TTS — the right place to
   extract lead fields and emit backend events.

3. **Removes ~600 LoC of custom adapter code** — `audio_io.py`, `providers/stt/`,
   `providers/llm/`, the per-turn loop, and the barge-in wiring all disappear or
   reduce to thin wrappers.

4. **Eliminates critical bugs** — the `caller_audio_stream = None` bug (issue #5),
   the `_tts_speak` dead stub (issue #12), the track subscription race (issue #4),
   the barge-in detection gap, and the audio accumulation loop all become non-issues
   because the framework handles them.

5. **Conversation coordinators are unaffected** — `VoiceSession`, `CallState`,
   `LeadCaptureStateMachine`, `EscalationDetector`, `RetrievalCoordinator`,
   `TourBookingCoordinator`, `FollowUpEmailCoordinator`, and `SummaryBuilder` are
   pure Python with no LiveKit dependency. They continue to function as before,
   invoked from the callbacks.

---

## Architecture

```
Twilio PCMU 8kHz
    ↓ WebSocket (Twilio Media Streams)
[services/api/app/bridge/livekit_bridge.py]
    mu-law decode → resample 8kHz→16kHz PCM → LiveKit room publish
    ↓ LiveKit room audio track
[VoicePipelineAgent] (framework-managed)
    ├── Silero VAD (detects utterance boundaries)
    ├── Deepgram STT (streaming, nova-2-phonecall)
    ├── before_llm_cb
    │     ├── EscalationDetector.check()
    │     ├── LeadCaptureStateMachine.advance()
    │     ├── RetrievalCoordinator.do_retrieve()
    │     └── inject grounded system prompt into ChatContext
    ├── Gemini 2.0 Flash (via livekit-plugins-google)
    ├── after_llm_cb
    │     ├── extract lead fields
    │     ├── emit backend events (fire-and-forget)
    │     └── flush transcript segment
    ├── ElevenLabs TTS (via livekit-plugins-elevenlabs)
    └── [barge-in, silence handling — framework built-in]
    ↓ LiveKit room audio track (agent speech)
[services/api/app/bridge/livekit_bridge.py]
    PCM 16kHz → mu-law encode → resample 16kHz→8kHz → Twilio Media Stream
    ↓ Twilio PCMU 8kHz → caller's phone
```

---

## Consequences

**Positive:**
- All 14 critical worker issues from the prior diagnostic are addressed
- ~600 LoC deleted; worker entrypoint shrinks from 685 lines to ~250 lines
- Framework handles barge-in, silence, VAD, audio track management
- Clear callback integration points for conversation coordinators

**Negative / Risks:**
- `VoicePipelineAgent` API may change between LiveKit Agents versions. Pin to a
  tested minor version in `pyproject.toml`.
- `before_llm_cb` and `after_llm_cb` are async; any exception raised inside them
  must be caught and handled — an unhandled exception will abort the LLM call and
  produce silence, not a graceful error message.

---

## References

- `docs/adr/0005-deepgram-replaces-whisper.md`
- `services/voice-agent/voice_agent/worker/entrypoint.py` (refactored)
- LiveKit Agents VoicePipelineAgent: https://docs.livekit.io/agents/voice-pipeline/
