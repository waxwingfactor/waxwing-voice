# Voice Agent — Known Blockers

Tracks open environment/integration blockers for the voice agent. Not a TODO list for Akhil — these need work from outside the voice-agent service to clear.

## 1. Integration tests skipped (environment, not code)

**Where:** `services/voice-agent/tests/test_backend_integration.py` — 6 tests skip-marked.

**Why:** The voice-agent venv does not have `sqlalchemy`, `asyncpg`, or `pgvector` installed. These are needed to mount Harsha's FastAPI app in-process via `httpx.AsyncClient` + `ASGITransport`.

**Fix (Akhil-side, ~10 minutes):** Add an optional dependency group to `services/voice-agent/pyproject.toml`:

```toml
[project.optional-dependencies]
integration = [
    "sqlalchemy>=2.0",
    "asyncpg>=0.29",
    "pgvector>=0.3",
    "aiosqlite>=0.20",
]
```

Then `uv sync --extra integration` and rerun pytest. Deferred to keep the prod runtime lean; pick this up before declaring Phase 1 done.

## 2. Docker Desktop engine not running

**Where:** `docker compose up -d db` from repo root.

**Why:** `open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file` — Docker Desktop's Linux engine isn't started on this Windows machine.

**Fix (Akhil-side):** Start Docker Desktop, then:

```
docker compose up -d db
cd services/api && make migrate && uvicorn app.main:app --reload
```

The compose file is well-formed; this is a local env issue, not a code issue. Could also be replaced with `subbu` provisioning a shared dev DB.

## 3. RESOLVED — CallCreateResponse.created_at (Phase 5, Harsha commit a42dad3)

**Where:** `services/api/app/schemas/calls.py::CallCreateResponse`

**Resolution:** `services/api/app/api/calls.py` now returns `CallCreateResponse.model_validate(call)`, where `call` is the SQLAlchemy ORM object. `created_at` is an auto-set DB column and is populated by the ORM before `model_validate` runs. The schema and route are consistent. Voice agent's `BackendClient.CallCreateResponse` mirror intentionally omits `created_at` because the voice agent has no use for it — this is correct behavior. No action needed.

## 4. Harsha's search-knowledge route is a stub (Phase 3 dependency)

**Where:** `POST /v1/voice/search-knowledge` in `services/api/app/api/voice.py`.

**Status:** Returns empty `results: []` for Phase 1 — real RAG retrieval is Phase 3 (Harsha + Subbu pgvector + ingestion pipeline).

**Impact:** Voice agent can call the route safely; conversation flow needs to handle empty-results gracefully (prompt fallback language, escalation if knowledge is required). Already covered by `system_prompt.py` "say I do not have that information" rules.

## 5. RESOLVED — Provider credentials (Phase 1 finish-up complete, 2026-05-09)

**Resolution:** All three provider adapters are now implemented with the mock/real pattern:

- `_tts_speak`: **IMPLEMENTED** — `ElevenLabsTTSAdapter` (requires `ELEVENLABS_API_KEY`) or `MockTTSAdapter` (`TTS_PROVIDER=mock`)
- `_stt_transcribe`: **IMPLEMENTED** — `WhisperSTTAdapter` (requires `WHISPER_API_KEY`) or `MockSTTAdapter` (`STT_PROVIDER=mock`)
- `_llm_respond`: **IMPLEMENTED** — `GeminiLLMAdapter` (requires `GEMINI_API_KEY`) or `MockLLMAdapter` (`LLM_PROVIDER=mock`)

**Required env vars for production (Subbu to provision):**
- `ELEVENLABS_API_KEY` — ElevenLabs API key from the team account
- `ELEVENLABS_VOICE_ID` — optional; defaults to Bella (`EXAVITQu4vr4xnSDxMaL`)
- `WHISPER_API_KEY` — OpenAI API key for hosted Whisper transcription
- `GEMINI_API_KEY` — Google Gemini API key for Gemini-2.0 Flash
- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` — LiveKit Cloud project (ADR-0004)

**Local dev without API keys (fully supported):**

```
STT_PROVIDER=mock LLM_PROVIDER=mock TTS_PROVIDER=mock python -m voice_agent
```

No network calls, no keys needed. All 9 call scenarios exercise correctly in this mode.

**Remaining gap:** `livekit-agents>=0.10` is declared in `pyproject.toml` but not installed in the venv (network not available during this session). The package must be installed (`uv sync`) before the worker can connect to LiveKit. All unit tests pass without it because `voice_agent/worker/` uses try/except import guards.

## 6. Design follow-ups from Phase 2 (Akhil — small, do before Phase 5)

These are not blocking; they are open polish items flagged by the Phase 2 agent.

a) **Confidence threshold (0.4) should move to `Settings`** so Subbu can tune via env var without a code change. Currently a module-level constant in `voice_agent/conversation/confidence.py` with constructor override.

b) ~~`EscalationReason.UNKNOWN` for emotional distress is too coarse.~~ **RESOLVED.** Voice-agent adds `EscalationReason.CALLER_DISTRESS` → `HandoffUrgency.HIGH` in `_ESCALATION_URGENCY_MAP`. Harsha is adding the matching enum value to `services/api/app/schemas/voice_tools.py::EscalationReason` on his side.

c) **`captured_fields` → `CallState.lead_fields` sync is manual in tests.** `VoiceSession` needs an explicit sync method (e.g. `_sync_lead_fields()`) once Phase 3's `_llm_respond` extracts structured fields from LLM output. Currently a no-op because there's no LLM.

## 7. Voice agent JWT minting strategy unclear (Phase 5, needs Subbu+Harsha decision)

**Where:** `services/voice-agent/voice_agent/config.py` → `voice_agent_jwt` setting.

**Issue:** Harsha's Phase 5 auth migration (commit `a42dad3`) replaces the `X-Company-Id` raw-UUID header with Bearer JWT auth. The voice agent now needs a valid HS256-signed JWT in `VOICE_AGENT_JWT` env var. How and when this token is minted is undecided.

**Options being considered:**

1. **Offline mint (simplest):** Subbu runs `auth.create_access_token(company_id, expires_in=timedelta(days=365))` once offline, stores the resulting token as a secret in the deployment environment. Simple but requires a manual rotation process and can't easily be invalidated.

2. **Startup mint via token endpoint:** On service startup, voice agent calls a `POST /v1/auth/service-token` endpoint (or similar) with service credentials (API key or mTLS cert) to mint a fresh JWT. Automatic refresh when approaching expiry. Requires Harsha to add a service-auth endpoint.

3. **Per-call mint:** Each call session mints a short-lived JWT. Overhead is negligible at call-level granularity. Requires Harsha's token endpoint to be available at call start.

**Impact:** Without a minted token in `VOICE_AGENT_JWT`, the voice agent cannot authenticate to the backend in staging or production. All 10 voice tools return 401.

**Owner:** Subbu (provisioning) + Harsha (token endpoint, if option 2 or 3). Akhil to update `config.py` and optionally `session.py` once strategy is decided.

**Action:** Resolve before declaring Phase 1 done in staging. Default TTL from `create_access_token()` is 24h — even option 1 works for MVP if Subbu provisions rotation.

## 8. Audio resampling in Harsha's backend (new — Phase 1 finish-up, 2026-05-09)

**Where:** `services/api/app/` — Harsha's `/ws/twilio/media` WebSocket bridge (ADR-0004).

**Issue:** Twilio Media Streams delivers audio as mu-law encoded at 8kHz. The voice-agent's `WhisperSTTAdapter` expects 16kHz 16-bit PCM. Resampling is Harsha's responsibility (boundary: he owns the bridge). Standard options:

- **`audioop.ulaw2lin` + `audioop.ratecv`** — Python stdlib (available in Python 3.12, deprecated in 3.13). Simple, no extra deps.
- **`numpy` + linear interpolation** — more control, already a common indirect dependency.
- **`scipy.signal.resample`** — higher-quality resampling, adds a dep to Harsha's service.

**Recommendation for Harsha:** `audioop` is simplest for MVP. If Python 3.13 migration is planned, switch to `numpy` resampling before that.

**Impact on voice-agent:** None — the voice-agent sees 16kHz PCM from LiveKit regardless of how Harsha resampled it. This blocker is documented here for cross-team awareness.

**Owner:** Harsha (backend bridge). Subbu to verify Python version in staging before `audioop` is used.

---

## Resolved in Phase 5 (Harsha commit a42dad3)

**Duplicate-side-effect risk on book_tour / send_email / request_handoff:** Previously, if the voice agent retried these endpoints after a network timeout, it could create duplicate bookings, emails, or handoff records. Harsha's Phase 5 commit adds server-side idempotency guards on all three endpoints (semantic dedup by `call_id` + slot/lead key). The voice agent's retry-once policy is now safe on these endpoints.

---

## Related decisions (ADRs)

Architecture decisions that affect this file's blocker landscape:

- [ADR-0001](../../docs/adr/0001-elevenlabs-replaces-vibevoice.md) — ElevenLabs Turbo v2.5 replaces VibeVoice as the locked TTS provider. Resolves the "VibeVoice credentials" blocker; replaces it with `ELEVENLABS_API_KEY` (§5 above).
- [ADR-0002](../../docs/adr/0002-resend-replaces-sendgrid.md) — Resend replaces SendGrid. No voice-agent code change; email provider is invisible to this service.
- [ADR-0003](../../docs/adr/0003-local-deployment-for-mvp-demo.md) — Local deployment for MVP demo. Cloudflare Tunnel provides the public Twilio webhook URL. Docker Compose provides the database. Subbu owns the demo runbook.
- [ADR-0004](../../docs/adr/0004-twilio-livekit-bridge-architecture.md) — Twilio→LiveKit bridge. Backend bridges Twilio Media Streams into LiveKit rooms; voice-agent runs as a LiveKit Agents worker. Accepted 2026-05-09.

---

**Last updated:** 2026-05-09 (Phase 1 finish-up: STT/LLM/TTS adapters implemented; ADR-0004 accepted; §5 provider-credentials resolved; §8 audio resampling blocker added for Harsha)
