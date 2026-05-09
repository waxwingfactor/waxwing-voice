# Harsha Project Document: Backend

## 1. Mission

Harsha owns the backend system that makes Waxwing Voice reliable, auditable, and useful. The backend is the source of truth for properties, calls, leads, documents, bookings, emails, and workflow state.

Primary scope:

- Backend API
- Database schema
- Voice tool endpoints
- Dashboard endpoints
- RAG ingestion and retrieval
- Calendar and email adapters
- Validation
- Audit logging
- Background jobs

## 2. Locked Tools

Use:

- Python
- FastAPI
- Uvicorn
- Pydantic v2
- SQLAlchemy 2.x
- Alembic
- uv
- Ruff
- pytest
- PostgreSQL
- pgvector
- S3-compatible object storage
- SendGrid (transactional email)
- Google Calendar (calendar adapter)

Do not introduce:

- Another backend framework
- Another ORM
- Another database
- Another vector database
- Direct provider calls from the frontend
- Direct database writes from the voice agent
- Node.js, Fastify, Express, NestJS, Prisma, or TypeORM for the MVP backend

## 3. Deliverables by Phase

### Phase 0 — Backend Foundation
**Git commit:** `6caf642`

**What was built:**

- `services/api/pyproject.toml` — all dependencies locked (FastAPI, SQLAlchemy 2.x, asyncpg, pgvector, Alembic, pydantic-settings, pdfplumber, python-docx, openai, httpx, ruff, pytest)
- `services/api/app/models/` — 12 SQLAlchemy ORM models:
  - `company.py`, `user.py`, `property.py`, `call.py`, `lead.py`
  - `booking.py`, `email_record.py`, `document.py`, `knowledge_chunk.py`
  - `transcript_segment.py`, `call_event.py`, `audit_log.py`
- `services/api/alembic/versions/0001_initial_properties_calls.py` — creates `companies`, `users`, `properties`, `calls` tables; unique constraint `uq_calls_twilio_call_sid`
- `tests/fixtures/seed_property.py` — inserts one sample company + property for local testing
- `docker-compose.yml` — pgvector/pg16 service for local dev

**Multi-tenant design:** every model scoped by `company_id` and `property_id` where applicable; all queries enforce both.

---

### Phase 1 — Live API
**Git commit:** `1412cc6`

**What was built:**

**Infrastructure layer:**
- `app/config.py` — pydantic-settings `Settings` class, reads from `.env`, `@lru_cache` singleton
- `app/database.py` — async SQLAlchemy engine, `get_db` dependency, `get_company_id` header extractor, `APIError` custom exception
- `app/main.py` — FastAPI app, CORS middleware, `APIError` → JSON error envelope handler, `/health` endpoint

**Error shape (all endpoints):**
```json
{"error": {"code": "PROPERTY_NOT_FOUND", "message": "...", "retryable": false}}
```

**Voice tool endpoints** (`app/api/voice.py` — `POST /v1/voice/*`):

| Endpoint | Status | Notes |
|---|---|---|
| `POST /v1/voice/search-knowledge` | Stub → Live (Phase 3) | Returns empty list until Phase 3 |
| `POST /v1/voice/leads` | Live | Upsert on `(property_id, phone)` via `ON CONFLICT DO UPDATE` |
| `POST /v1/voice/events` | Live | Append-only write to `call_events` |
| `POST /v1/voice/transcript-segment` | Live | Append-only write to `transcript_segments` |
| `POST /v1/voice/call-summary` | Live | Upserts AI fields on `Call` row by `call_id` |
| `POST /v1/voice/check-availability` | Stub → Live (Phase 4) | Returns 3 hardcoded slots until Phase 4 |
| `POST /v1/voice/book-tour` | Live | Writes `Booking` row; calendar call added Phase 4 |
| `POST /v1/voice/send-email` | Live | Writes `EmailRecord`; real dispatch added Phase 4 |
| `POST /v1/voice/request-handoff` | Live | Updates `Call.escalation_status`; writes `AuditLog` |

**Dashboard + call lifecycle endpoints:**
- `app/api/calls.py` — `POST /v1/calls/`, `PATCH /v1/calls/{id}`, `GET /v1/calls/`, `GET /v1/calls/{id}`
- `app/api/properties.py` — `GET /v1/properties/`, `GET /v1/properties/{id}`, `GET /v1/properties/{id}/summary`

**Schemas:** `app/schemas/voice_tools.py` — all 10 request/response Pydantic schema pairs

**Migration:** `0002_add_remaining_tables.py` — creates: `transcript_segments`, `call_events`, `leads` (unique: `uq_leads_property_phone`), `bookings`, `email_records`, `documents`, `knowledge_chunks` (pgvector `Vector(1536)`), `audit_logs`

---

### Phase 2 — Data Model and Dashboard Foundation

Phase 2 work was delivered as part of Phase 1. All lead, booking, summary, and dashboard endpoints were built in the same pass.

**What was built (included in Phase 1 commit):**
- Lead upsert with full qualification fields (budget, move-in date, unit type, pet info, urgency, lead score)
- `call-summary` endpoint with AI-generated fields (primary intent, sentiment, action items, escalation flag)
- Dashboard list endpoints for calls and properties with pagination
- `PaginatedResponse[T]` generic response wrapper
- Full error code vocabulary: `PROPERTY_NOT_FOUND`, `CALL_NOT_FOUND`, `LEAD_NOT_FOUND`, `BOOKING_SLOT_UNAVAILABLE`, `UNSUPPORTED_FILE_TYPE`, `DOCUMENT_PARSE_FAILED`

---

### Phase 3 — RAG Knowledge Base

**What was built:**

**RAG pipeline** (`app/rag/`):
- `extractor.py` — `extract_text(file_bytes, file_type) -> str`; PDF via `pdfplumber`, DOCX via `python-docx`, TXT via UTF-8 decode; local imports per branch to avoid loading unused deps
- `chunker.py` — `chunk_text(text, source_label, chunk_size=1800)` splits on newline boundaries, skips chunks < 50 chars; returns `[{chunk_text, source_label, page_number}]`
- `embedder.py` — `async embed_texts(texts, api_key)` calls OpenAI `text-embedding-ada-002`; falls back to `[[0.0] * 1536]` zero vectors when `api_key` is empty (app never crashes without a key)

**Document upload endpoint** (`app/api/documents.py`):
- `POST /v1/documents/upload` — multipart form: `property_id` + file
- Validates extension: `{pdf, docx, txt}`; extracts text; chunks; embeds; inserts `Document` row + `KnowledgeChunk` rows; marks `processing_status="indexed"`
- `storage_key` stored as logical path `{property_id}/{doc_id}/{filename}` (no real S3 in hackathon scope)
- Response: `{document_id, file_name, chunk_count, processing_status}`
- Error codes: `PROPERTY_NOT_FOUND` (404), `UNSUPPORTED_FILE_TYPE` (400), `DOCUMENT_PARSE_FAILED` (422)

**Live RAG retrieval** (`app/api/voice.py` — `search-knowledge`):
- Embeds query via `embed_texts`
- Runs raw pgvector cosine similarity: `1 - (embedding <=> CAST(:query_vec AS vector))`
- Multi-tenant safe: WHERE clause always filters by both `property_id` AND `company_id`
- Returns up to `top_k` results with `similarity_score` clamped to `[0.0, 1.0]`
- Graceful degradation: returns empty results if `OPENAI_API_KEY` not configured

**Config additions** (`app/config.py`):
- `openai_api_key: str = ""` — reads from `OPENAI_API_KEY` env var

**New `.env` variables required:**
```
OPENAI_API_KEY=sk-...
```

---

### Phase 4 — Workflow Automation (Google Calendar + SendGrid)

**What was built:**

**Google Calendar adapter** (`app/integrations/google_calendar.py`):
- `get_free_slots(calendar_id, service_account_path, start_date, end_date)` — queries freebusy API, generates 30-min slots 09:00–17:30 UTC, returns up to 10 free slots
- `create_calendar_event(calendar_id, service_account_path, summary, start_dt, end_dt, attendee_email)` — creates event with `sendUpdates="all"` to trigger invite emails; returns Google event ID
- All sync Google API calls wrapped in `asyncio.run_in_executor` to avoid blocking the event loop
- Graceful degradation: raises `RuntimeError("Google Calendar not configured")` when credentials missing; callers fall back to stub slots

**SendGrid email adapter** (`app/integrations/email.py`):
- `send_sendgrid_email(to_email, subject, body, api_key, from_email) -> bool`
- POSTs to SendGrid v3 REST API via `httpx.AsyncClient` (no new dependency — httpx is a transitive dep via `fastapi[standard]`)
- Returns `True` on HTTP 200/202; `False` on any exception or missing credentials; never raises

**Live voice endpoints** (`app/api/voice.py`):
- `check-availability` — real Google Calendar freebusy query; falls back to 3 stub slots on any exception
- `book-tour` — real calendar event creation; booking DB row always written even if calendar fails; writes `AuditLog(action="TOUR_BOOKED")`
- `send-email` — real SendGrid dispatch; `delivery_status` = `"sent"` / `"failed"` / `"pending"` (no email on file); writes `AuditLog(action="EMAIL_SENT")`

**Config additions** (`app/config.py`):
- `google_service_account_path: str = ""` — reads from `GOOGLE_SERVICE_ACCOUNT_PATH`
- `google_calendar_id: str = ""` — reads from `GOOGLE_CALENDAR_ID`
- `sendgrid_api_key: str = ""` — reads from `SENDGRID_API_KEY`
- `sendgrid_from_email: str = ""` — reads from `SENDGRID_FROM_EMAIL`

**New `.env` variables required:**
```
GOOGLE_SERVICE_ACCOUNT_PATH=/absolute/path/to/service-account.json
GOOGLE_CALENDAR_ID=primary
SENDGRID_API_KEY=SG....
SENDGRID_FROM_EMAIL=you@yourdomain.com
```

---

### Phase 5 — Pilot Readiness (JWT Auth, Rate Limiting, Dedup Hardening)

**What was built:**

**JWT Authentication** (`app/database.py`, `app/auth.py`, `app/config.py`):
- Replaced trusted `X-Company-Id` header with cryptographic Bearer token verification
- `get_company_id` dependency now uses `HTTPBearer` + `PyJWT` — verifies signature, checks expiry, extracts `company_id` claim; raises `401 UNAUTHORIZED` on any failure
- `app/auth.py` — `create_access_token(company_id, expires_in?)` helper for tests and seeding (24-hour default expiry)
- Algorithm: `HS256`, key: `SECRET_KEY` env var
- Zero router changes — all 5 routers already used `Depends(get_company_id)`; swapping the dependency implementation updated all endpoints automatically

**New `.env` variables required:**
```
SECRET_KEY=your-production-secret-key-here
```
(`JWT_ALGORITHM` defaults to `HS256` and does not need to be set explicitly)

**Rate Limiting** (`app/limiter.py`, `app/main.py`, `app/api/voice.py`):
- Added `slowapi>=0.1.9` — wraps the `limits` library, integrates natively with FastAPI
- `app/limiter.py` — shared `Limiter` instance (IP-keyed in Phase 5; Phase 6 upgrades to Redis + company-keyed counters)
- `app/main.py` — wired `app.state.limiter`, `SlowAPIMiddleware`, `RateLimitExceeded` → `429` handler
- All 9 voice endpoints decorated with rate limits matching the contract spec:

| Endpoint | Limit |
|---|---|
| `POST /transcript-segment` | 120/minute |
| All other 8 voice endpoints | 60/minute |

**Migration** `0003_phase5_dedup_constraints.py`:
- Partial unique index on `bookings(lead_id, tour_date, start_time) WHERE status != 'cancelled'` — prevents double-booking without blocking rebooking after cancellation
- Partial unique index on `email_records(call_id, template_type, recipient) WHERE call_id IS NOT NULL` — prevents duplicate emails per call per template
- Both use `op.execute()` — Alembic's `create_unique_constraint` has no WHERE predicate support

**Idempotency guards** (`app/api/voice.py`):

| Endpoint | Guard | Mechanism |
|---|---|---|
| `book_tour` | Checks for active booking on `(lead_id, tour_date, start_time, status != 'cancelled')` before inserting | Returns existing `booking_id`; Google Calendar not called again |
| `send_follow_up_email` | Checks for existing `email_record` on `(call_id, template_type, recipient)` before dispatching | Returns existing `email_id`; SendGrid not called again |
| `request_human_handoff` | Checks `audit_logs` for `HANDOFF_REQUESTED` on same `call_id` within last 30 seconds | Returns existing `handoff_id`; no duplicate audit row |
| `POST /v1/calls/` | `twilio_call_sid` unique constraint in DB (migration 0001) | DB rejects duplicates at insert time |

**Contract documentation** (`docs/contracts/voice-tools.md`):
- P95 latency targets table for all 11 tools with voice-agent timeout values
- Full idempotency section documenting which endpoints are safe to retry and how

---

## 4. API Contract Responsibilities

Harsha owns request and response schemas for:

- Voice tools (`app/schemas/voice_tools.py`)
- Dashboard data (`app/api/calls.py`, `app/api/properties.py`)
- Document uploads (`app/api/documents.py`)
- Calendar booking (via voice `book-tour`)
- Email delivery (via voice `send-email`)
- Handoff actions (via voice `request-handoff`)

Every endpoint defines:

- Method and path
- Auth requirement (`Authorization: Bearer <JWT>` — verified via `get_company_id` dependency)
- Pydantic request schema
- Pydantic response schema
- Error shape with stable error code
- Example request / example response
- Idempotency behavior

Full contract: `docs/contracts/voice-tools.md`

---

## 5. Core Models

| Model | File | Key Constraints |
|---|---|---|
| Company | `app/models/company.py` | — |
| User | `app/models/user.py` | scoped to company |
| Property | `app/models/property.py` | scoped to company |
| Call | `app/models/call.py` | `uq_calls_twilio_call_sid` |
| Lead | `app/models/lead.py` | `uq_leads_property_phone` |
| Booking | `app/models/booking.py` | partial unique: `(lead_id, tour_date, start_time) WHERE status != 'cancelled'` |
| EmailRecord | `app/models/email_record.py` | partial unique: `(call_id, template_type, recipient) WHERE call_id IS NOT NULL` |
| Document | `app/models/document.py` | scoped to property + company |
| KnowledgeChunk | `app/models/knowledge_chunk.py` | `Vector(1536)`, no FK to documents (chunks survive document deletion) |
| TranscriptSegment | `app/models/transcript_segment.py` | append-only |
| CallEvent | `app/models/call_event.py` | append-only |
| AuditLog | `app/models/audit_log.py` | append-only, no PII in `metadata_` |

---

## 6. RAG Architecture

```
POST /v1/documents/upload
        │
        ▼
  extract_text()          ← pdfplumber / python-docx / UTF-8
        │
  chunk_text()            ← ~1800 chars, newline-boundary splits
        │
  embed_texts()           ← OpenAI text-embedding-ada-002 (1536-dim)
        │                    zero-vector fallback if no API key
        ▼
  knowledge_chunks table  ← pgvector Vector(1536), scoped by property_id + company_id

POST /v1/voice/search-knowledge
        │
  embed_texts([query])
        │
  SELECT ... ORDER BY embedding <=> CAST(:query_vec AS vector) LIMIT :top_k
        │
  → [{chunk_text, source_label, page_number, similarity_score}]
```

Retrieval always scopes by both `property_id` AND `company_id` — cross-tenant leakage is structurally impossible.

---

## 7. Workflow Automation Architecture

```
check-availability  →  get_free_slots()       ← Google Calendar freebusy API
                        └── fallback: 3 stub slots

book-tour           →  create_calendar_event() ← Google Calendar events.insert
                        └── booking DB row always written (calendar failure non-fatal)
                        └── AuditLog(action="TOUR_BOOKED")

send-email          →  send_sendgrid_email()   ← SendGrid v3 REST via httpx
                        └── delivery_status = sent / failed / pending
                        └── AuditLog(action="EMAIL_SENT")

request-handoff     →  call.escalation_status = "escalated"
                        └── AuditLog(action="HANDOFF_REQUESTED")
```

All Google API calls are sync-wrapped in `asyncio.run_in_executor` to protect the async event loop.

---

## 8. Migrations

| File | Revision | What it creates |
|---|---|---|
| `0001_initial_properties_calls.py` | 0001 | `companies`, `users`, `properties`, `calls` |
| `0002_add_remaining_tables.py` | 0002 | `transcript_segments`, `call_events`, `leads`, `bookings`, `email_records`, `documents`, `knowledge_chunks`, `audit_logs` |
| `0003_phase5_dedup_constraints.py` | 0003 | Partial unique indexes on `bookings` and `email_records` |

Run all migrations:
```bash
cd services/api && uv run alembic upgrade head
```

---

## 9. Dependencies

Depends on Akhil for:

- Voice tool usage needs and call lifecycle events
- Lead extraction payloads and summary payloads
- Latency-sensitive endpoint feedback (P95 targets in `docs/contracts/voice-tools.md`)

Depends on Alex for:

- Dashboard field requirements, filter and sorting needs
- Document upload UX needs

Depends on Subbu for:

- PostgreSQL + pgvector provisioning
- Object storage (S3) for future real document storage
- SendGrid and Google Calendar credentials in production env
- Deployment, logs, and background job runtime

---

## 10. Environment Variables

| Variable | Phase | Description |
|---|---|---|
| `DATABASE_URL` | 0 | PostgreSQL connection string |
| `ENVIRONMENT` | 0 | `local` / `staging` / `production` |
| `SECRET_KEY` | 5 | JWT signing secret (`HS256`); set a strong value in production |
| `JWT_ALGORITHM` | 5 | JWT algorithm; defaults to `HS256`, no override needed unless changing algorithm |
| `OPENAI_API_KEY` | 3 | OpenAI key for embeddings; empty = zero-vector fallback |
| `GOOGLE_SERVICE_ACCOUNT_PATH` | 4 | Absolute path to GCP service account JSON |
| `GOOGLE_CALENDAR_ID` | 4 | Calendar ID (e.g. `primary`) |
| `SENDGRID_API_KEY` | 4 | SendGrid API key (`SG....`) |
| `SENDGRID_FROM_EMAIL` | 4 | Verified sender address in SendGrid |

Copy `services/api/.env.example` and fill in values before starting the server.

---

## 11. Validation and Error Rules

Backend validates every request body via Pydantic. Errors follow the canonical envelope:

```json
{
  "error": {
    "code": "BOOKING_SLOT_UNAVAILABLE",
    "message": "The selected tour slot is no longer available.",
    "retryable": false
  }
}
```

Rules:
- Error codes are stable strings (never change after shipping)
- `retryable` tells Akhil's agent whether to retry automatically
- No PII in error messages or `AuditLog.metadata_`
- External provider errors are caught and converted; raw provider errors never surface to callers

---

## 12. Definition of Done

A backend feature is done when:

- It has a Pydantic schema (request + response)
- It validates input at the boundary
- It scopes data by `company_id` and `property_id`
- It has a success path and at least one error path with a stable error code
- It writes `AuditLog` rows for all durable/sensitive actions
- It passes `ruff check` and `ruff format --check`
- It has a Postman test or pytest coverage
- It is documented in `docs/contracts/voice-tools.md` if consumed by Akhil or Alex
