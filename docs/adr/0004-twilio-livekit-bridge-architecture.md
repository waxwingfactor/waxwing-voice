# ADR-0004: Twilio → LiveKit Bridge Architecture

**Status:** Accepted  
**Date:** 2026-05-09  
**Authors:** Akhil (voice-agent), Harsha (backend)  
**Impacted owners:** Akhil (LiveKit Agents worker), Harsha (Twilio webhook + bridge), Subbu (LiveKit credentials, infrastructure)

---

## Context

Twilio Media Streams delivers caller audio as a WebSocket stream of mu-law encoded audio frames
(8kHz, 8-bit) to a public HTTPS webhook endpoint. The voice agent must consume this audio, run it
through Whisper STT, Gemini LLM, and ElevenLabs TTS, and return synthesised audio back to the
caller — all in real time with sub-second latency targets.

The critical design question is: **how does Twilio's audio reach the voice-agent process?**

The voice-agent is a Python service that uses the LiveKit Agents framework for session lifecycle
management, VAD (voice activity detection), barge-in handling, and audio track management.
LiveKit Agents workers receive audio via LiveKit room subscriptions, not via raw WebSocket frames.

Three patterns were considered.

---

## Options Considered

### Option A: Direct WebSocket Bridge (custom protocol)

**Pattern:** Harsha's backend accepts the Twilio Media Streams WebSocket and forwards raw frames
directly to the voice-agent service over a second WebSocket connection (custom protocol).

**Pros:**
- Simple topology — only two services involved
- No LiveKit dependency for audio I/O path
- Lower infrastructure cost (no LiveKit Cloud)

**Cons:**
- Voice-agent must understand Twilio's frame format (mu-law base64-encoded JSON envelopes)
- Session lifecycle (call start, caller disconnect, DTMF events) must be reimplemented in the
  custom protocol — duplicating what LiveKit Agents provides
- Barge-in (VAD) must be implemented from scratch
- Harder to test: no standard framework; mock WebSocket server needed
- Couples voice-agent to Twilio's wire format, violating the "voice-agent unaware of telephony"
  ownership boundary

### Option B: LiveKit Room as Intermediary (selected)

**Pattern:** Harsha's backend (`/ws/twilio/media`) accepts the Twilio Media Streams WebSocket,
decodes mu-law audio, resamples to 16kHz PCM, and publishes it to a per-call LiveKit room as an
audio track. The voice-agent runs as a LiveKit Agents worker that subscribes to these rooms and
processes audio using the standard Agents framework.

**Pros:**
- Aligned with locked stack: LiveKit Agents is already the required voice orchestration framework
- Voice-agent sees only a LiveKit `AudioStream` — completely unaware of Twilio frame format
- LiveKit Agents framework provides VAD, barge-in events, participant lifecycle out of the box
- Clean ownership boundary: Harsha owns the Twilio-side bridge; Akhil owns the Agents worker
- Standard framework makes testing easier: mock LiveKit room for unit tests
- LiveKit supports multi-participant rooms — future resident-facing features (screen share,
  co-browsing) can be added without architecture changes

**Cons:**
- Requires a LiveKit Cloud project (or self-hosted LiveKit server) — Subbu must provision
- Adds `livekit-agents` as a voice-agent dependency (already declared in pyproject.toml)
- Harsha's backend needs `livekit-server-sdk` to create rooms and publish audio tracks
- Adds one network hop (Twilio → backend → LiveKit → voice-agent) vs. direct bridge
- LiveKit Cloud latency adds ~20–50ms per audio frame in typical deployments

### Option C: In-Process (rejected early)

**Pattern:** Voice-agent and Twilio webhook handler run in the same process.

**Rejected because:** Couples two services with different scaling, restart, and deployment
lifecycles. Twilio webhook handler must be reachable by Twilio's servers (public HTTPS); the
voice-agent worker needs a long-running process model. Combining them creates a fragile monolith.
Not considered seriously.

---

## Decision

**Option B: LiveKit bridge.**

The backend's `/ws/twilio/media` endpoint accepts Twilio Media Streams, decodes mu-law audio,
resamples to 16kHz 16-bit PCM, and publishes it to a per-call LiveKit room. The voice-agent runs
as a LiveKit Agents worker that subscribes to these rooms.

### Data flow

```
Caller
  │ (PSTN)
  ▼
Twilio ──── POST /v1/voice/twilio ──────────► Harsha's backend
              (TwiML: <Connect><Stream>)
  │
  │ WebSocket (Twilio Media Streams)
  ▼
Harsha's backend  /ws/twilio/media
  │  ┌──────────────────────────────────────┐
  │  │ 1. Accept Twilio WS                  │
  │  │ 2. Decode mu-law → 16kHz PCM         │
  │  │ 3. Create LiveKit room (call SID)    │
  │  │ 4. Publish caller AudioTrack         │
  │  └──────────────────────────────────────┘
  │
  │ LiveKit room (per-call, named by Twilio call SID)
  ▼
Voice-agent LiveKit Agents worker
  │  ┌──────────────────────────────────────┐
  │  │ 1. Receive caller AudioStream        │
  │  │ 2. Whisper STT → caller_text         │
  │  │ 3. VoiceSession.handle_caller_turn() │
  │  │ 4. Gemini LLM → response_text        │
  │  │ 5. ElevenLabs TTS → audio chunks     │
  │  │ 6. Publish agent AudioTrack          │
  │  └──────────────────────────────────────┘
  │
  │ LiveKit room → Harsha's backend (subscribed to agent track)
  ▼
Harsha's backend → Twilio (re-encode PCM → mu-law, stream back)
  │
  ▼
Caller hears agent response
```

### Room metadata contract

Harsha sets room metadata as a JSON string on room creation. The voice-agent worker reads these
fields from `JobContext.room.metadata`:

```json
{
  "property_id": "<uuid>",
  "company_id": "<uuid>",
  "twilio_call_sid": "CA...",
  "caller_phone": "+1...",
  "livekit_room_id": "<room-name>"
}
```

The voice-agent must not proceed if `property_id` is missing or malformed.

### Audio pipeline sample rates

| Segment | Format | Sample Rate |
|---------|--------|-------------|
| Twilio → backend | mu-law, 8-bit | 8 kHz |
| Backend → LiveKit room | 16-bit PCM | 16 kHz |
| LiveKit → voice-agent Whisper | 16-bit PCM | 16 kHz |
| ElevenLabs TTS output | 16-bit PCM | 16 kHz |
| Voice-agent → LiveKit room | 16-bit PCM | 16 kHz |
| LiveKit → backend → Twilio | mu-law | 8 kHz |

Resampling responsibility: **Harsha's backend** handles 8kHz↔16kHz conversion at the
Twilio bridge. The voice-agent operates exclusively at 16kHz and is unaware of mu-law encoding.

---

## Consequences

### Positive
- Voice-agent code has no Twilio dependencies — it only imports `livekit-agents`
- LiveKit Agents framework handles VAD, barge-in detection, and participant events natively
- Per-call room isolation means calls cannot cross-contaminate
- Framework's `JobContext` provides a clean per-job lifecycle (start, run, teardown)

### Negative / Trade-offs
- **LiveKit infrastructure required:** Subbu must provision a LiveKit Cloud project (or
  self-hosted LiveKit) with URL, API key, and API secret before Phase 1 can run end-to-end
- **Backend work for Harsha:** `/ws/twilio/media` WebSocket handler, room creation, audio track
  publishing, and reverse path (agent audio → Twilio) are all Harsha's responsibility
- **Extra latency hop:** LiveKit adds ~20–50ms. Acceptable for phone calls (300ms+ RTT already)
- **livekit-agents version pinning:** The worker uses `livekit-agents>=0.10`; Subbu must ensure
  the LiveKit server version is compatible

### Open items (tracked in BLOCKERS.md)
- Subbu: provision `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`
- Harsha: implement `/ws/twilio/media` bridge, room creation, audio publishing, reverse path
- Audio resampling library in Harsha's backend: `audioop` (stdlib, Python 3.12) or `numpy`

---

## Rollback Plan

If LiveKit operations become a burden (cost, reliability, complexity), we can fall back to
Option A (direct WebSocket bridge) in approximately 2 days of rework:

1. Harsha replaces the LiveKit bridge with a direct WS forwarder to the voice-agent
2. Akhil replaces `voice_agent/worker/` with a raw WebSocket server that speaks a simple
   JSON frame protocol
3. VAD/barge-in must be reimplemented (likely using `silero-vad` or energy threshold)
4. All other voice-agent logic (VoiceSession, coordinators, BackendClient) is unchanged

The rollback does not require an ADR revision — it would be ADR-0005.

---

## Related ADRs

- [ADR-0001](0001-elevenlabs-replaces-vibevoice.md) — ElevenLabs Turbo v2.5 as locked TTS provider
- [ADR-0002](0002-resend-replaces-sendgrid.md) — Resend replaces SendGrid (no voice-agent impact)
- [ADR-0003](0003-local-deployment-for-mvp-demo.md) — Local deployment + Cloudflare Tunnel for MVP demo
