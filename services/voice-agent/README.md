# Voice Agent Service

**Owner:** Akhil
**Stack:** Twilio (telephony) -> LiveKit Agents -> Whisper (STT) -> Gemini-3.0 Flash (LLM) -> VibeVoice (TTS)
**Phase:** 0 — skeleton (no live provider connections)

---

## Folder Boundaries

Write here for:
- LiveKit Agent code (`voice_agent/agent/`)
- Call state model (`voice_agent/state/`)
- System prompt assembly (`voice_agent/prompts/`)
- Backend tool HTTP client (`voice_agent/tools/`)
- Voice smoke tests (`tests/`)

Do NOT write here for:
- Direct database writes
- Frontend UI
- Backend API implementation (`services/api/` — Harsha owns)
- Provider account setup (`infra/` — Subbu owns)

All durable actions go through `voice_agent/tools/backend_client.py` -> Harsha's FastAPI endpoints.

---

## Package Layout

```
services/voice-agent/
  pyproject.toml          # uv-managed, Ruff + pytest config
  voice_agent/
    __init__.py
    __main__.py           # entry point: python -m voice_agent
    config.py             # Settings loaded from env vars
    logging_config.py     # structlog (JSON for prod, console for dev)
    agent/
      __init__.py
      session.py          # VoiceSession — one per call (Phase 0: stubs)
    state/
      __init__.py
      call_state.py       # CallState, LeadFields, TranscriptSegment, enums
    prompts/
      __init__.py
      system_prompt.py    # build_system_prompt() + safety rules
    tools/
      __init__.py
      backend_client.py   # BackendClient — HTTP wrapper for Harsha's APIs
  tests/
    __init__.py
    test_call_state.py    # Unit tests: Pydantic models
    test_system_prompt.py # Unit tests: prompt assembly + safety rules
    manual/
      README.md           # Index of manual test scenarios
      01_rent_availability.md
      02_pet_parking.md
      03_tour_booking.md
      04_maintenance.md
      05_unknown_question.md
      06_fair_housing_escalation.md
      07_tool_failure_recovery.md
      08_barge_in.md
```

---

## Prerequisites

- Python 3.12 (check: `python --version`)
- `uv` installed (check: `uv --version`)
  Install: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

---

## Install

From `services/voice-agent/`:

```bash
uv sync --extra dev
```

This installs all runtime and dev dependencies (pytest, ruff, etc.) into a
local `.venv/`. `uv` reads `pyproject.toml` — no separate `requirements.txt`.

---

## Environment Variables

Phase 0 runs without any env vars set. For Phase 1+, copy the template
and fill in values. Subbu will publish `.env.example` at the repo root
with canonical variable names.

```bash
cp .env.example .env   # once Subbu publishes .env.example
```

### Variable Reference

| Variable | Required phase | Description |
|----------|---------------|-------------|
| `BACKEND_API_URL` | Phase 0+ | Base URL for Harsha's FastAPI. Default: `http://localhost:8000` |
| `BACKEND_API_TIMEOUT_SECONDS` | Phase 0+ | Per-request timeout. Default: `10.0` |
| `LIVEKIT_URL` | Phase 1 | `wss://...` LiveKit server URL |
| `LIVEKIT_API_KEY` | Phase 1 | LiveKit API key |
| `LIVEKIT_API_SECRET` | Phase 1 | LiveKit API secret — never log this |
| `TWILIO_ACCOUNT_SID` | Phase 1 | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | Phase 1 | Twilio Auth Token — never log this |
| `GEMINI_API_KEY` | Phase 1 | Google Gemini API key — never log this |
| `GEMINI_MODEL` | Phase 1 | Default: `gemini-2.0-flash` |
| `WHISPER_API_KEY` | Phase 1 | OpenAI hosted Whisper key — never log this |
| `ELEVENLABS_API_KEY` | Phase 1 | ElevenLabs API key (ADR-0001) — never log this |
| `ELEVENLABS_VOICE_ID` | Phase 1 | Voice preset. Default: Bella (`EXAVITQu4vr4xnSDxMaL`) |
| `TTS_PROVIDER` | Phase 0+ | `elevenlabs` (default) or `mock` for local dev without an API key |
| `VOICE_AGENT_JWT` | Phase 1 | Backend auth token (offline-minted). See `BLOCKERS.md §7` |
| `DEFAULT_PROPERTY_ID` | Local testing | Fallback property ID for local dev |
| `SILENCE_TIMEOUT_SECONDS` | Phase 1 | Seconds before silence handler fires. Default: `3.0` |
| `LOG_LEVEL` | Phase 0+ | `DEBUG`, `INFO`, `WARNING`. Default: `INFO` |
| `LOG_FORMAT` | Phase 0+ | `json` (prod) or `console` (local dev). Default: `json` |

Secrets (`LIVEKIT_API_SECRET`, `TWILIO_AUTH_TOKEN`, `GEMINI_API_KEY`,
`WHISPER_API_KEY`, `ELEVENLABS_API_KEY`, `VOICE_AGENT_JWT`) must never appear
in logs or be committed to source control.

---

## Running the Agent (Phase 0)

```bash
# From services/voice-agent/
LOG_FORMAT=console python -m voice_agent
```

Expected output:
```
========================================
 Waxwing Voice Agent — Phase 0 Skeleton
========================================
 No provider connections are live yet.
 Phase 1 will wire: Twilio -> LiveKit -> Whisper -> Gemini -> VibeVoice

 Run `pytest` to verify call-state model and prompt builder.
========================================
```

The process exits with code 0 after printing. Phase 1 will run as a long-lived
LiveKit Agent worker instead of exiting.

---

## Running Tests

```bash
# From services/voice-agent/
pytest

# With coverage report
pytest --cov=voice_agent --cov-report=term-missing

# Verbose output
pytest -v
```

All tests in `tests/` are unit tests — no backend connection, no provider
credentials needed. They should pass from a clean `uv sync --extra dev`.

Phase 1 will add integration tests that require a running backend (Harsha's
staging endpoint or a local FastAPI instance).

---

## Linting and Formatting

```bash
# Check
ruff check voice_agent/ tests/

# Fix auto-fixable issues
ruff check --fix voice_agent/ tests/

# Format
ruff format voice_agent/ tests/
```

Ruff is configured in `pyproject.toml`. Same settings as Harsha's backend.

---

## Manual Voice Test Scripts

See `tests/manual/README.md` for the 8 required test scenarios.

These require Phase 1+ to be live with a Twilio number and staging credentials.

---

## Phase 1 Setup Notes (for when you're ready)

Once Subbu provides staging credentials:

1. Fill in `.env` with LiveKit, Twilio, Gemini, Whisper, VibeVoice credentials.
2. Start Harsha's backend locally or point `BACKEND_API_URL` at staging.
3. Run `python -m voice_agent` — it will start the LiveKit Agent worker.
4. Call the Twilio staging number from a real phone.
5. Check dashboard for transcript and call event records.

LiveKit Agent worker will be a long-running process (does not exit). Use
Subbu's process manager / Docker setup for staging deployment.

---

## Design Decisions (Phase 0)

**Package name:** `voice_agent` (underscore, not hyphen) — Python import convention.

**Call state in memory:** `CallState` is held in the `VoiceSession` for the
duration of one call. It is never written to the database directly. Durable
persistence happens exclusively through `BackendClient` tool calls. This enforces
the boundary documented in `docs/03-tooling-and-guardrails.md`.

**Prompt safety rules embedded in code:** Safety guardrails (Fair Housing,
no invented prices, escalation triggers) are written into `system_prompt.py`,
not just documented. Every call gets them. They cannot be accidentally omitted
by changing a config flag.

**`BackendToolError.retryable`:** Distinguishes transient failures (backend 500,
network timeout — retry once) from permanent failures (invalid schema, property
not found — escalate or fail gracefully without retry). Phase 2 will add retry
loop in `VoiceSession`.

**`faster-whisper` dependency:** Uses the `faster-whisper` library which wraps
the Whisper model family for lower latency. This is still Whisper (same model
weights) — not a different STT provider. Locked stack is preserved. If Subbu
provides a Whisper API endpoint instead, Phase 1 will switch to `httpx` calls
with the same interface.

---

## What Is Explicitly Deferred to Phase 1

- LiveKit Agents worker bootstrap and room event loop
- Twilio webhook receiver (TwiML response to route call to LiveKit)
- Whisper STT integration (audio bytes -> transcript text)
- Gemini-3.0 Flash LLM integration (prompt -> response text)
- VibeVoice TTS integration (text -> audio stream -> LiveKit)
- Barge-in detection and TTS stream cancellation
- Silence detection timer
- Real `httpx.AsyncClient` calls in `BackendClient` (replacing stubs)
- Call lifecycle event emission (`call_started`, `intent_detected`, etc.)
- Real-time transcript segment flushing

See `docs/01-phased-implementation-plan.md` for the full Phase 1 checklist.
