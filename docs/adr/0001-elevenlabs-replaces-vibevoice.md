# ADR-0001: ElevenLabs Replaces VibeVoice as the Locked TTS Provider

Date: 2026-05-09
Status: Accepted

## Context

Phase 0 locked VibeVoice as the TTS provider for the Waxwing Voice pipeline. VibeVoice is engineered for long-form podcast-style synthesis — multi-speaker sessions up to approximately 90 minutes, optimized for studio-quality audio at the cost of streaming latency. That design center is structurally wrong for telephony.

Real-time inbound phone calls impose hard constraints that VibeVoice cannot meet:

- **Sub-300ms time-to-first-byte (TTFB).** The caller hears silence otherwise. VibeVoice's architecture batches audio before streaming, producing TTFBs measured in seconds.
- **Short utterances, not long-form.** Voice agent responses are 1–4 sentences. VibeVoice's strengths are long coherent segments, not rapid short-burst synthesis.
- **Mid-stream cancellation for barge-in.** When a caller interrupts, the TTS stream must be cancelled immediately. VibeVoice offers no WebSocket-level cancel signal; the only option is connection teardown, which adds hundreds of milliseconds of bleed.
- **No self-hosting burden.** VibeVoice self-hosting requires a GPU instance with proven VRAM headroom and consistent inference latency. For an MVP demo running on a laptop with Cloudflare Tunnel (ADR-0003), self-hosting a GPU inference stack is not viable.

The conclusion is that VibeVoice is the wrong tool for this job. This ADR records the decision to replace it.

## Options

**Option 1: Keep VibeVoice, run a spike.**
Invest 2–4 days testing VibeVoice's streaming API against telephony latency targets. Risk: high — the fundamental architecture (batch-and-stream vs. streaming-first) is unlikely to meet <300ms TTFB regardless of configuration. Self-hosting GPU adds ops overhead beyond MVP scope.

**Option 2: ElevenLabs Turbo v2.5 via `livekit-plugins-elevenlabs`.**
ElevenLabs Turbo v2.5 is built for real-time applications. Documented TTFB ~75ms. The `livekit-plugins-elevenlabs` package is an official LiveKit plugin that drops in as a TTS source in the Agents framework. WebSocket streaming supports mid-stream cancellation for barge-in. Hosted API — no GPU ops. Risk: per-character billing; vendor lock-in; voice cloning ethical/legal surface area (less severe than VibeVoice's multi-speaker design).

**Option 3: PlayHT.**
Streaming-first, good phone-quality voices, competitive latency (~150–200ms TTFB). No official LiveKit plugin — requires a custom adapter. Slightly higher DX friction than ElevenLabs for this stack.

**Option 4: Azure TTS (Neural).**
Mature streaming TTS with <200ms TTFB on Neural voices. Official Azure SDK. No LiveKit plugin — custom adapter required. Adds Azure dependency on a team that is not yet using Azure infrastructure. Billing is simpler (per-character, lower rate) but setup cost is higher.

**Option 5: Google Cloud TTS.**
Same streaming characteristics as Azure. No LiveKit plugin. Adds another Google cloud service alongside Gemini, which is plausible but not compelling enough to offset the integration cost for MVP.

## Decision

**ElevenLabs Turbo v2.5 via `livekit-plugins-elevenlabs`.**

Reasons:

1. **Streaming-first design with ~75ms TTFB.** Meets the telephony sub-300ms requirement with margin.
2. **Official LiveKit plugin.** `livekit-plugins-elevenlabs` is maintained by LiveKit and drops into the Agents framework as a typed TTS source. Adapter code is minimal.
3. **Barge-in via WebSocket cancellation.** ElevenLabs' streaming WebSocket API supports mid-stream cancel, which maps directly to the `TTSAdapter.cancel()` interface in this implementation.
4. **Hosted API — no GPU ops.** Aligns with the local MVP demo deployment model (ADR-0003).
5. **Phone-quality voices well-tested.** ElevenLabs Turbo v2.5 voice "Bella" (`EXAVITQu4vr4xnSDxMaL`) has documented telephone-grade quality at low bitrate.

## Consequences

**Technical:**
- `ElevenLabsTTSAdapter` implements the `TTSAdapter` protocol in `services/voice-agent/voice_agent/providers/tts/`.
- `VoiceSession._tts_speak` delegates to the injected `TTSAdapter` instead of calling VibeVoice directly.
- `livekit-plugins-elevenlabs` added to `services/voice-agent/pyproject.toml` under main dependencies.
- VibeVoice fields (`vibevoice_api_key`, `vibevoice_api_url`) removed from `voice_agent/config.py`; ElevenLabs fields added (`elevenlabs_api_key`, `elevenlabs_voice_id`, `tts_provider`).

**Operational:**
- Per-character billing at approximately $0.18 per 1,000 characters (ElevenLabs Turbo v2.5 rate as of 2026-05-09). At typical leasing call response density (~500 chars/call), cost is negligible at MVP pilot scale.
- New environment variables: `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`.
- Subbu provisions the ElevenLabs account and API key. Default voice is Bella (`EXAVITQu4vr4xnSDxMaL`) unless overridden by `ELEVENLABS_VOICE_ID`.

**Documentation updates required:**
- `docs/00-project-document.md` §2 and §7.1 — TTS row updated from VibeVoice to ElevenLabs.
- `docs/03-tooling-and-guardrails.md` §2, §6, §7 — TTS row and guardrail bullets updated.
- `services/voice-agent/.env.example` — VibeVoice vars replaced with ElevenLabs vars.

**Vendor risk:**
- ElevenLabs voice cloning capability creates ethical/legal surface area. Waxwing Voice uses synthesized stock voices only — no cloning. System prompt must never instruct the agent to impersonate a real person.
- Vendor lock-in is mitigated by the `TTSAdapter` protocol: swapping to a different provider requires a new adapter class, not changes to the session or conversation logic.

## Rollback Plan

TTS is isolated behind the `TTSAdapter` protocol (`services/voice-agent/voice_agent/providers/tts/protocol.py`). Any compliant implementation can replace `ElevenLabsTTSAdapter`:

1. Write a new adapter class implementing `TTSAdapter` (e.g., `PlayHTTTSAdapter`).
2. Change `tts_provider` in config or env to the new value.
3. Update `pyproject.toml` dependencies.

Estimated cost: approximately 1 day to swap to PlayHT or Azure TTS if ElevenLabs disappoints during pilot. No changes to `session.py`, `state_machine.py`, or any conversation logic.

## Impacted Owners

- **Akhil:** TTS adapter implementation, `session.py` update, config changes, this ADR. (Write zone: `services/voice-agent/`)
- **Subbu:** ElevenLabs account creation, `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` provisioning in all environments, `.env.example` publication.
- **Harsha:** No code changes required. Backend is unaffected — TTS is purely a voice-agent concern.
- **Alex:** No code changes required. Frontend does not touch TTS.
