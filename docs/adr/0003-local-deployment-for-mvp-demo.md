# ADR-0003: Local Deployment for MVP Demo

Date: 2026-05-09
Status: Accepted

## Context

The Phase 5 implementation plan assumes staging and eventually pilot-production environments — containerized services deployed to a cloud host, with a stable public URL for Twilio webhooks. For the upcoming MVP demo, that full provisioning path adds friction without proportional value.

The MVP demo has a specific and limited scope: demonstrate one inbound leasing call, handled end-to-end by the voice agent, with a person watching in the room. That scenario requires:

- One concurrent call at a time (not horizontal scale)
- A routable public URL that Twilio can send webhook events to
- The backend API, database, and voice agent running and reachable
- No uptime SLA — if the demo machine reboots mid-demo, that is a demo failure, not a production incident

Full cloud deployment for this scenario adds: account provisioning, IAM setup, container registry, deployment pipelines, DNS, TLS certificates, environment parity verification, and monitoring. That work is appropriate for Phase 6 (pilot production) but unnecessary for a single controlled demo.

## Options

**Option 1: Full cloud staging deployment.**
Complete the Phase 6 infrastructure work now. Correct for the long term but adds 1–2 weeks of Subbu's time before the demo can run. High setup cost, low marginal value for a single demo in a controlled room.

**Option 2: Local deployment with Cloudflare Tunnel.**
Run all services on the demo laptop. Use `docker compose up -d db` for Postgres+pgvector. Run the backend with `uvicorn`. Run the voice agent with `python -m voice_agent`. Use Cloudflare Tunnel (`cloudflared`) to expose a stable HTTPS URL to Twilio without opening firewall ports or configuring DNS. Zero cloud infra provisioning required.

**Option 3: Hybrid — cloud database, local services.**
Run Postgres on a cloud host (e.g., Supabase free tier or a shared dev DB), run backend and voice agent locally. Reduces local database management. Adds a cloud dependency that could fail if the shared DB is unavailable. Not worth the complexity for one demo.

## Decision

**Local-only deployment for the MVP demo.**

Specific topology:

- `docker compose up -d db` — Postgres 15 + pgvector, local container, port 5432
- `uvicorn app.main:app --reload` — Harsha's FastAPI backend, port 8000
- `python -m voice_agent` — Akhil's voice agent, port 8080 (or as configured)
- `cloudflared tunnel --url http://localhost:8080` — Cloudflare Tunnel provides the public HTTPS URL used in the Twilio webhook configuration

Twilio's webhook points to the Cloudflare Tunnel URL. All other services communicate over localhost. All credentials live in `.env` files on the demo laptop.

Cloud deployment becomes Phase 6 work and is not blocked by this decision.

## Consequences

**Runtime dependencies:**
- `cloudflared` must be installed on the demo laptop. The Cloudflare Tunnel URL changes on each `cloudflared` restart unless a named tunnel is configured. Subbu's demo runbook must include the Twilio webhook update step if the URL changes.
- Docker Desktop (or Docker Engine) must be running for the database container.

**Concurrency:**
- Single concurrent call. If two calls arrive simultaneously, behavior is undefined. This is acceptable for a demo where one person is calling.

**Reliability:**
- Demo machine reliability is the availability SLA. Laptop sleep, WiFi drops, and low battery are real failure modes. The demo runbook must cover: laptop power connected, WiFi stable, sleep disabled, services pre-started and verified before the demo call.

**Credentials:**
- All API keys (`ELEVENLABS_API_KEY`, `LIVEKIT_API_KEY`, `TWILIO_AUTH_TOKEN`, `GEMINI_API_KEY`, `VOICE_AGENT_JWT`, etc.) live in `.env` files on the demo laptop. These must not be committed to source control.

**Phase 6 path:**
- This decision is forward-only. Cloud deployment in Phase 6 supersedes local deployment entirely. No rollback is needed — local deployment simply stops being used once Phase 6 infrastructure is stable.

**Documentation:**
- Subbu owns `docs/runbooks/demo-setup.md`. That runbook must cover: dependency installation (`cloudflared`, Docker), service startup sequence, environment variable checklist, Twilio webhook update step, and smoke test verification before the demo call.

## Rollback Plan

This decision is forward-only by design. The rollback is cloud deployment (Phase 6). If the demo fails due to local environment issues, the immediate mitigation is the demo runbook's troubleshooting section, not a different deployment model.

## Impacted Owners

- **Subbu:** `cloudflared` setup and demo runbook (`docs/runbooks/demo-setup.md`). Tunnel URL update in Twilio console before each demo session if using ephemeral tunnels. Provision demo laptop `.env` files with all required credentials.
- **Akhil:** Local `.env` setup for the voice agent. Verify `python -m voice_agent` starts cleanly from `services/voice-agent/`. Ensure `ELEVENLABS_API_KEY` and all Phase 1 provider keys are in the demo laptop `.env`.
- **Harsha:** Verify `make dev` or `uvicorn app.main:app --reload` starts the backend cleanly from `services/api/`. Confirm `make migrate` runs against the local Docker database without errors.
- **Alex:** Frontend is not required for the voice demo. Dashboard may be run locally if a walkthrough is planned, but it is not on the critical path.
