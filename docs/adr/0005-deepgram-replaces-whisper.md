# ADR-0005: Deepgram Replaces Whisper for STT

**Status:** Accepted  
**Date:** 2026-05-09  
**Authors:** Akhil (voice pipeline)  
**Impacted owners:** Akhil (refactor), Subbu (Deepgram account in production environments)

---

## Context

The voice pipeline requires a speech-to-text (STT) provider that can transcribe
caller audio in real time, emit partial results for barge-in detection, and
integrate cleanly with the LiveKit Agents framework.

The prior implementation used OpenAI Whisper via `WhisperSTTAdapter`
(`services/voice-agent/voice_agent/providers/stt/whisper.py`). That adapter had a
critical architectural flaw: **Whisper's REST API (`/v1/audio/transcriptions`) is
batch-only**. The adapter accumulated all audio from the LiveKit `AudioStream` into
an in-memory buffer and submitted it as a single WAV file at the end of each
utterance. Consequences:

1. **Never-ending accumulation** — on a live audio stream there is no clean "end of
   utterance" signal at the transport level. The adapter accumulated indefinitely and
   never called the Whisper API during a real call, effectively blocking the STT stage
   forever (critical issue #3).

2. **No partial transcripts** — barge-in detection depended on `is_final=False` events
   which the Whisper batch endpoint cannot produce. The adapter always emitted only one
   `is_final=True` event per call.

3. **Latency** — even when working correctly, round-tripping a multi-second WAV file
   adds 2–8 s of latency before the LLM can begin generating a response.

4. **Framework mismatch** — the LiveKit Agents `VoicePipelineAgent` pattern (ADR-0006)
   expects a streaming STT plugin with a well-defined async interface. The custom
   `WhisperSTTAdapter` reimplemented plumbing the framework already provides.

Additionally, the `faster-whisper` package (self-hosted variant) was listed as a
dependency but never wired in, and self-hosting adds infrastructure burden.

---

## Options Considered

| Option | Streaming | Partial transcripts | Latency | LiveKit plugin | Cost |
|--------|-----------|---------------------|---------|----------------|------|
| Whisper + VAD batching | Simulated | No | 2–8 s | Custom adapter | OpenAI pricing |
| OpenAI Realtime API | Yes | Yes | ~300 ms | livekit-plugins-openai | ~$0.06/min |
| **Deepgram nova-2-phonecall** | **Yes** | **Yes** | **~150 ms** | **livekit-plugins-deepgram** | **$0.0043/min** |
| AssemblyAI | Yes | Partial | ~250 ms | Custom adapter | ~$0.012/min |
| Self-hosted faster-whisper | Simulated | No | 1–3 s | Custom adapter | Infrastructure cost |

---

## Decision

**Use Deepgram with the `nova-2-phonecall` model via the `livekit-plugins-deepgram`
official plugin.**

```python
from livekit.plugins import deepgram

stt = deepgram.STT(
    model="nova-2-phonecall",
    language="en",
    interim_results=True,
    smart_format=True,
    punctuate=True,
)
```

---

## Rationale

1. **Streaming-first design** — Deepgram's WebSocket API delivers incremental
   transcripts as audio arrives. Each 100–200 ms audio chunk produces a partial result.
   The `livekit-plugins-deepgram` plugin integrates this directly with LiveKit's
   `VoicePipelineAgent` barge-in and silence detection infrastructure.

2. **~150 ms TTFT** — time-to-first-transcript is roughly 150 ms from end of utterance,
   enabling the LLM to start generating within half a second. This is the
   industry-standard benchmark for real-time telephony.

3. **Telephony-tuned model** — `nova-2-phonecall` is trained on phone audio at 8 kHz
   and specifically handles the audio quality characteristics of Twilio PCMU streams.
   Standard models trained on studio audio perform measurably worse on phone calls.

4. **Partial transcripts enable real barge-in** — `interim_results=True` means the
   `VoicePipelineAgent` receives in-progress transcripts. The framework uses these to
   detect when a caller has begun speaking and cancels TTS output immediately, reducing
   the "talking over each other" latency from several seconds to ~200 ms.

5. **Free $200 credit covers the full demo period** — at $0.0043/min, $200 buys
   approximately 775 hours of transcription. The MVP demo period will not exceed this.

6. **Official LiveKit plugin = drop-in** — `livekit-plugins-deepgram` is maintained by
   LiveKit and is the recommended STT integration for `VoicePipelineAgent`. Swapping
   it in removes ~150 lines of custom adapter code.

7. **Single dependency addition** — the only new voice-agent dependency is
   `livekit-plugins-deepgram`. The Deepgram key is already in
   `services/voice-agent/.env` as `DEEPGRAM_API_KEY`.

---

## Consequences

**Positive:**
- Removes `services/voice-agent/voice_agent/providers/stt/whisper.py` (~330 LoC)
- Removes `services/voice-agent/voice_agent/providers/stt/mock.py` (tests now mock
  at the `VoicePipelineAgent` level)
- Removes `faster-whisper` and `openai` from voice-agent dependencies
- Aligns with `VoicePipelineAgent` framework idioms (ADR-0006)
- Real barge-in capability through partial transcripts

**Negative / Risks:**
- New vendor dependency (Deepgram). Mitigated: swapping STT is a one-line change
  within the `VoicePipelineAgent` constructor thanks to the plugin abstraction.
- Deepgram account must be provisioned in production by Subbu. The key is already
  present in the local `.env`.

---

## Rollback

If Deepgram is unavailable, swap to OpenAI Whisper streaming (which is available via
`livekit-plugins-openai`) by changing one line in the entrypoint:

```python
# From:
stt = deepgram.STT(model="nova-2-phonecall", ...)

# To:
from livekit.plugins import openai as lk_openai
stt = lk_openai.STT(model="whisper-1")
```

The `VoicePipelineAgent` abstraction means no other code changes are needed.

---

## References

- `docs/adr/0004-twilio-livekit-bridge-architecture.md`
- `docs/adr/0006-voice-pipeline-agent-pattern.md`
- `services/voice-agent/voice_agent/worker/entrypoint.py` (refactored)
- Deepgram pricing: https://deepgram.com/pricing (verified 2026-05-09)
- LiveKit Agents plugins: https://docs.livekit.io/agents/plugins/
