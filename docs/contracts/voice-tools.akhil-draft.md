# Voice Tool Contracts — Akhil's Original Draft

> **SUPERSEDED by `docs/contracts/voice-tools.md` as of 2026-05-09.**
> Harsha published authoritative Pydantic schemas in `services/api/app/schemas/voice_tools.py`.
> Most open questions resolved by Harsha's email reply + commit `581d6f3a` (2026-05-09).
> `BackendClient` and `CallState` updated to match Harsha's real contract.
> This file is retained for traceability of open questions and field-name diffing only.
> Do NOT use field names or shapes from this document — use `voice-tools.md` and
> `services/api/app/schemas/voice_tools.py` as source of truth.

---

## Phase 5 reconciliation (2026-05-09)

Harsha's Phase 5 commit (`a42dad3`) introduced three breaking changes that required voice-agent updates. All changes are confined to `services/voice-agent/`.

### Auth change: X-Company-Id -> Bearer JWT

- **Before (Phase 0/1):** All requests sent `X-Company-Id: <raw-uuid>` header.
- **After (Phase 5):** All requests send `Authorization: Bearer <jwt>` where the JWT is HS256-signed by `SECRET_KEY`, contains `{"company_id": "<uuid>", "exp": <timestamp>}`, and has a 24h TTL by default.
- **Updated files:** `voice_agent/tools/backend_client.py` (constructor `company_id` -> `jwt_token`, `_auth_headers()`, `__aenter__`), `voice_agent/agent/session.py` (`company_id` -> `jwt_token`), `voice_agent/config.py` (added `voice_agent_jwt` setting).
- **Pending:** JWT minting strategy (offline vs. token endpoint vs. per-call) — tracked in `BLOCKERS.md §7`.

### Rate limiting now active

- **60 req/min** on most endpoints (slowapi default, per `services/api/app/limiter.py`).
- **120 req/min** on `POST /v1/voice/transcript-segment` (higher allowance for high-frequency STT writes).
- **429 response shape:** `{"detail": "Rate limit exceeded"}` with `Retry-After: <seconds>` header. The voice agent's `BackendClient._parse_error()` now handles 429 by raising `BackendToolError(code="RATE_LIMITED", retryable=True, retry_after_seconds=<int|None>)`. The retry-once policy will sleep `retry_after_seconds` before the second attempt.

### Server-side idempotency guards on mutating endpoints

- `book_tour`, `send_follow_up_email`, and `request_human_handoff` now have server-side dedup by semantic keys (call_id + slot/lead key). This closes the duplicate-side-effect concern: if the voice agent retries one of these after a network timeout, the backend will detect and suppress the duplicate action.
- The `Idempotency-Key` client header (OQ-14) is no longer needed — server-side dedup covers the retry case without client coordination.

### OQ-2 resolution updated (was X-Company-Id, now JWT)

OQ-2 (auth format) was previously marked RESOLVED for Phase 1 with X-Company-Id. That resolution is superseded. The current auth is Bearer JWT (HS256, company_id claim, 24h TTL). See updated row in the table below.

### OQ-14 resolution

OQ-14 (Idempotency-Key header semantics) is now RESOLVED via server-side guards rather than client-side headers. See updated row in the table below.

---

## Open Questions from Akhil Draft — Resolution Status (updated 2026-05-09 post-Harsha-reply)

| # | Question | Status | Resolution |
|---|----------|--------|------------|
| 1 | **Path prefix:** Is `/v1/voice/` correct? | RESOLVED | Confirmed. Voice tool endpoints are `POST /v1/voice/*`. `get_property_profile` is `GET /v1/properties/{id}`. Call lifecycle is `POST /v1/calls/` (NOT under `/v1/voice/`). |
| 2 | **Auth:** Service-to-service JWT, API key, or mTLS? | RESOLVED (Phase 5) | Phase 1 used `X-Company-Id` raw UUID header. Phase 5 (Harsha commit `a42dad3`) replaced this with Bearer JWT: HS256-signed, `company_id` claim, 24h TTL. `BackendClient` updated. JWT minting strategy still pending (BLOCKERS.md §7). |
| 3 | **Call record creation:** Does `call_started` event create the call record, or is there a separate `POST /v1/voice/calls`? | RESOLVED | Voice agent POSTs `POST /v1/calls/` on session start using `CallCreateRequest`. Returns `call_id` immediately. (Twilio webhook path is the Phase 5 production approach; Phase 1 stays in voice-agent.) |
| 4 | **`ai_summary` in save_call_summary:** Backend want agent's Gemini summary or generate its own? | RESOLVED | Harsha's `SaveCallSummaryRequest` includes a `summary` field (max 5000 chars). Voice agent sends the Gemini-generated summary. `ai_summary` -> `summary`. |
| 5 | **Segment idempotency:** Voice agent include its own `segment_id` or backend generates? | RESOLVED | Backend generates. `SaveTranscriptSegmentRequest` has no client-supplied ID field. |
| 6 | **Email `context` shape:** Confirm required fields for each template_type. | NOT RESOLVED | Harsha's `SendFollowUpEmailRequest.context` is `dict[str, Any]` with no per-template validation. **Still open: need per-template context schemas from Harsha.** |
| 7 | **SLO enforcement:** Timeout and retry strategy during live calls. | RESOLVED | Harsha's email pinned P95 targets: transcript-segment & call-event ≤500ms; leads upsert ≤1s; book-tour & send-email ≤2s (async fire-and-forget internally); handoff ≤1s. To be added to `voice-tools.md` in the routes PR. |

---

## Additional Open Questions Raised by Reading Harsha's Real Schemas (2026-05-09)

| # | Question | Status | Resolution |
|---|----------|--------|------------|
| 8 | **`get_property_profile` response field name mismatch** | RESOLVED | Wire field is `id`. Harsha commit `581d6f3a` renamed `PropertyProfileResponse.property_id` → `id` to match `PropertyDetailResponse`. `BackendClient.PropertyProfileResponse` mirror updated. |
| 9 | **`CallCreateRequest` endpoint missing from voice-tools.md** | RESOLVED | Path is `POST /v1/calls/`. Voice agent calls this on session start. Returns `call_id`. (Same answer as OQ-3.) |
| 10 | **`CallEventType` enum completeness — NEEDS AKHIL REVIEW** | RESOLVED | Locked as-is. Harsha's additions kept (`knowledge_retrieved`, `tool_called`, `interruption_detected`, `silence_detected`). Akhil's `intent_detected`, `phase_changed`, `lead_field_captured` fold into `save_call_summary` payload instead of being separate events. NEEDS-AKHIL-REVIEW marker removed in commit `581d6f3a`. |
| 11 | **`LeadCreateRequest` vs `LeadFieldsInput` drift risk** | NOT RESOLVED | Defaults still differ between `leads.py::LeadCreateRequest` and `voice_tools.py::LeadFieldsInput`. Server-side validation falls back to unvalidated strings on `lead_status`/`lead_score`. **Still open: Harsha to align defaults and tighten server-side enum validation in routes PR.** |
| 12 | **`maintenance_acknowledgment` email template removed** | RESOLVED | No email path for resident maintenance — flow ends at `request_human_handoff` / internal record. `general` template is fallback if ever needed. Maintenance conversation flow must terminate at handoff. |
| 13 | **`HandoffUrgency` required — no default** | NOT RESOLVED (Akhil-side) | Akhil owns the mapping from internal `EscalationReason` to Harsha's `HandoffUrgency`. Proposed mapping (to be implemented in voice agent): `EMERGENCY -> emergency`, `FAIR_HOUSING -> high`, `ANGRY_CALLER -> high`, `LEGAL_QUESTION -> high`, `LOW_CONFIDENCE -> medium`, `TOOL_FAILURE -> medium`, default `medium`. No further Harsha input needed. |
| 14 | **`Idempotency-Key` header semantics** | RESOLVED (Phase 5) | Server-side dedup added on `book_tour`, `send_follow_up_email`, `request_human_handoff` by Harsha commit `a42dad3`. Client-side `Idempotency-Key` header is not needed. Existing retry-safe design for leads/events/segments unchanged. |

---

**Author:** Akhil (voice pipeline)
**Original Status:** DRAFT — awaiting Harsha review and Pydantic schema publication
**Date:** 2026-05-09
**Related doc:** `docs/00-project-document.md` section 10

---

## Purpose

This document proposes request/response shapes for the ten backend tool endpoints
that the voice agent calls during a live phone conversation. It is a **contract
request**, not a final spec. Harsha owns the authoritative Pydantic schemas in
`services/api/app/schemas`. Once those exist, Akhil will update the voice tool
HTTP client to match exactly.

## General Conventions

- All endpoints live under `/v1/voice/` (Harsha to confirm path prefix).
- All requests and responses are `application/json`.
- All timestamps are ISO 8601 with UTC timezone (`2025-09-01T14:30:00Z`).
- All property-scoped endpoints require `property_id` — this enforces tenant separation.
- All call-scoped endpoints require `call_id` — this correlates voice events to a call record.
- `call_id` is generated by the voice agent at call start (UUID4). The backend should
  accept it as the idempotency key and create the call record on first use.
- Harsha's backend is the source of truth for all persisted records. The voice agent
  holds in-memory state only for the duration of the call.

### Error shape (proposed)

```json
{
  "error": {
    "code": "TOOL_ERROR_CODE",
    "message": "Human-readable description",
    "retryable": true
  }
}
```

`retryable: true` tells the voice agent it is safe to retry after a short wait.
`retryable: false` means the voice agent should escalate or fail gracefully.

### Latency expectations

Voice conversations tolerate very little delay. Suggested SLOs:

| Tool | P95 target |
|------|------------|
| `get_property_profile` | < 200 ms |
| `search_property_knowledge` | < 600 ms |
| `save_transcript_segment` | < 300 ms |
| `create_call_event` | < 200 ms |
| `create_or_update_lead` | < 400 ms |
| `check_tour_availability` | < 800 ms |
| `book_tour` | < 1000 ms |
| `send_follow_up_email` | < 500 ms (async OK) |
| `save_call_summary` | < 500 ms |
| `request_human_handoff` | < 300 ms |

---

## Tool 1: `get_property_profile`

**When called:** Once at call start, to load property context into the system prompt.

### Request

```
GET /v1/voice/properties/{property_id}/profile
```

Query params: none. Property ID from path.

### Response (200)

```json
{
  "property_id": "prop_abc123",
  "name": "Maple Grove Apartments",
  "address": "123 Maple Ave, Austin, TX 78701",
  "description": "72-unit multifamily community in East Austin.",
  "amenities": ["pool", "gym", "dog park", "covered parking"],
  "office_hours": {
    "monday_friday": "9:00 AM - 6:00 PM",
    "saturday": "10:00 AM - 4:00 PM",
    "sunday": "closed"
  },
  "leasing_policies": {
    "application_fee": 50,
    "security_deposit": "one_month_rent",
    "lease_terms_available": ["6_month", "12_month"],
    "pet_policy": "cats and dogs allowed, max 2, under 50 lbs, $350 pet deposit"
  },
  "maintenance_instructions": "Call (512) 555-0100 for emergencies. Submit non-urgent requests through the resident portal.",
  "escalation_contacts": {
    "leasing_manager": "Jane Smith — jane@maplegrouve.com",
    "emergency_line": "(512) 555-0100"
  },
  "call_handling_rules": {
    "after_hours_greeting": "Our office is currently closed. I can answer questions and schedule a tour.",
    "max_tour_duration_minutes": 30
  }
}
```

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `PROPERTY_NOT_FOUND` | No property with this ID | No |
| `PROPERTY_INACTIVE` | Property is disabled | No |
| `UPSTREAM_ERROR` | Database/infra failure | Yes |

### Idempotency

Read-only. Safe to call multiple times.

---

## Tool 2: `search_property_knowledge`

**When called:** Phase 3+. Before each Gemini response when the caller asks a
question that may be answered by uploaded documents or structured knowledge.

### Request

```
POST /v1/voice/properties/{property_id}/knowledge/search
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "query": "what is the pet deposit for dogs?",
  "top_k": 4
}
```

### Response (200)

```json
{
  "chunks": [
    {
      "chunk_id": "chunk_xyz",
      "text": "Pets are welcome. Dog deposit is $350. Max weight 50 lbs.",
      "source_label": "Pet Policy Document",
      "page_number": 2,
      "relevance_score": 0.92
    }
  ],
  "query_id": "qry_uuid4"
}
```

### Notes

- `top_k` defaults to 4. Voice agent uses this to bound prompt size.
- `relevance_score` is 0–1. Voice agent should not present results below 0.5 as facts.
- `query_id` is for internal tracing — log it in voice agent traces.
- Empty `chunks` array = knowledge not found. Voice agent must say
  "I do not have that information" rather than guessing.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `PROPERTY_NOT_FOUND` | Property scope invalid | No |
| `RETRIEVAL_FAILED` | Vector search error | Yes |
| `UPSTREAM_ERROR` | Infra failure | Yes |

---

## Tool 3: `create_or_update_lead`

**When called:** During lead qualification (Phase 2+). Called each time the agent
captures a new lead field — partial updates must be accepted.

### Request

```
POST /v1/voice/leads
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "lead_fields": {
    "name": "Jordan Lee",
    "phone_number": "+15125550199",
    "email": "jordan.lee@email.com",
    "desired_move_in_date": "2025-09-01",
    "budget_min": 1200,
    "budget_max": 1600,
    "desired_unit_type": "1BR",
    "pet_info": "one dog, labrador, 45 lbs",
    "occupants": 1,
    "tour_interest": true,
    "urgency": "this_month",
    "preferred_contact_method": "email"
  }
}
```

### Response (200 or 201)

```json
{
  "lead_id": "lead_uuid4",
  "created": true,
  "updated_fields": ["name", "email", "tour_interest"]
}
```

**Idempotency:** `call_id` is the upsert key within a property. Calling with the
same `call_id` updates the existing lead record. Safe to call multiple times as
fields are filled in.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `PROPERTY_NOT_FOUND` | Invalid property scope | No |
| `INVALID_LEAD_FIELDS` | Schema validation failure | No |
| `UPSTREAM_ERROR` | DB failure | Yes |

---

## Tool 4: `create_call_event`

**When called:** Throughout the call for lifecycle events (call started, intent
detected, phase changed, escalation triggered, call ended). Powers the dashboard
activity feed.

### Request

```
POST /v1/voice/calls/{call_id}/events
```

```json
{
  "call_id": "call_uuid4",
  "property_id": "prop_abc123",
  "event_type": "intent_detected",
  "payload": {
    "intent": "leasing_inquiry",
    "confidence": 0.88
  },
  "occurred_at": "2025-09-01T14:32:00Z"
}
```

**Proposed event_type values:**

- `call_started`
- `intent_detected`
- `phase_changed`
- `lead_field_captured`
- `rag_query_executed`
- `tour_availability_checked`
- `tour_booked`
- `follow_up_email_sent`
- `escalation_triggered`
- `tool_failure`
- `call_ended`

### Response (201)

```json
{
  "event_id": "evt_uuid4",
  "accepted": true
}
```

**Idempotency:** If the backend receives the same `event_type` + `call_id` within
5 seconds, it may deduplicate. The voice agent should not rely on deduplication.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `CALL_NOT_FOUND` | call_id unknown | No (first event creates the call) |
| `INVALID_EVENT_TYPE` | Unknown event type | No |
| `UPSTREAM_ERROR` | DB failure | Yes |

**Note for Harsha:** The first `call_started` event should create the call record
if it does not exist, accepting `twilio_call_sid`, `caller_phone_number`,
`property_id`, and `livekit_room_id` in the payload.

---

## Tool 5: `save_transcript_segment`

**When called:** After each completed utterance (caller or agent), flushed in
near-real-time during the call.

### Request

```
POST /v1/voice/calls/{call_id}/transcript
```

```json
{
  "call_id": "call_uuid4",
  "speaker": "caller",
  "text": "Hi, I was wondering about the pet policy for dogs.",
  "timestamp": "2025-09-01T14:33:10Z"
}
```

`speaker` values: `"caller"` or `"agent"`.

### Response (201)

```json
{
  "segment_id": "seg_uuid4",
  "accepted": true
}
```

**Idempotency:** The voice agent includes its own `segment_id` in the payload
(Harsha to confirm whether to use it or generate a new one). This allows retry
without duplicating segments.

**PII note:** Transcript text is PII-adjacent. Backend should store behind
authenticated access. Voice agent must not log transcript text at INFO level.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `CALL_NOT_FOUND` | call_id unknown | Yes (race with call_started event) |
| `INVALID_SPEAKER` | Unknown speaker value | No |
| `UPSTREAM_ERROR` | DB failure | Yes |

---

## Tool 6: `save_call_summary`

**When called:** Once, at call end, before the agent disconnects.

### Request

```
POST /v1/voice/calls/{call_id}/summary
```

```json
{
  "call_id": "call_uuid4",
  "property_id": "prop_abc123",
  "primary_intent": "leasing_inquiry",
  "escalation_flag": false,
  "escalation_reason": null,
  "lead_fields": {
    "name": "Jordan Lee",
    "email": "jordan.lee@email.com",
    "tour_interest": true
  },
  "booking_confirmed": true,
  "booking_id": "booking_uuid4",
  "follow_up_email_sent": true,
  "duration_seconds": 187.4,
  "confidence_score_final": 0.82,
  "tool_failure_count": 0,
  "ai_summary": "Prospect Jordan Lee called about 1BR availability. Budget $1200-$1600. Booked a tour for Sept 15 at 10 AM. Follow-up email sent.",
  "action_items": [
    "Confirm tour with Jordan via email",
    "Check pet deposit policy for labs over 40 lbs"
  ]
}
```

**Note:** `ai_summary` and `action_items` are generated by Gemini in the closing
phase. These fields are optional for Harsha's schema (backend may generate its own
summary if preferred — Akhil to confirm).

### Response (200)

```json
{
  "summary_id": "sum_uuid4",
  "accepted": true
}
```

**Idempotency:** Only one summary per call. If called twice with the same `call_id`,
backend should update (not duplicate).

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `CALL_NOT_FOUND` | call_id unknown | No |
| `SUMMARY_ALREADY_EXISTS` | Already saved — will update | No (treat as success) |
| `UPSTREAM_ERROR` | DB failure | Yes |

---

## Tool 7: `check_tour_availability`

**When called:** Phase 4. Before presenting tour slots to the caller.

### Request

```
POST /v1/voice/properties/{property_id}/tours/availability
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "date_range": {
    "start_date": "2025-09-10",
    "end_date": "2025-09-20"
  },
  "duration_minutes": 30
}
```

### Response (200)

```json
{
  "available_slots": [
    {
      "slot_id": "slot_uuid4",
      "date": "2025-09-15",
      "time": "10:00 AM",
      "timezone": "America/Chicago",
      "duration_minutes": 30
    },
    {
      "slot_id": "slot_uuid5",
      "date": "2025-09-15",
      "time": "2:00 PM",
      "timezone": "America/Chicago",
      "duration_minutes": 30
    }
  ],
  "property_id": "prop_abc123"
}
```

**Voice agent behavior:** Present at most 3 slots to avoid overwhelming the caller.
If `available_slots` is empty, tell the caller no slots are available in that range
and offer to have the leasing team follow up.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `PROPERTY_NOT_FOUND` | Invalid property | No |
| `CALENDAR_UNAVAILABLE` | Calendar provider error | Yes |
| `INVALID_DATE_RANGE` | Dates in the past or malformed | No |
| `UPSTREAM_ERROR` | Infra failure | Yes |

---

## Tool 8: `book_tour`

**When called:** Phase 4. After caller confirms date, time, property, and contact
details. The voice agent must confirm all four before calling this.

### Request

```
POST /v1/voice/tours
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "lead_id": "lead_uuid4",
  "selected_slot": {
    "slot_id": "slot_uuid4",
    "date": "2025-09-15",
    "time": "10:00 AM",
    "timezone": "America/Chicago",
    "duration_minutes": 30
  }
}
```

### Response (201)

```json
{
  "booking_id": "booking_uuid4",
  "status": "confirmed",
  "calendar_event_id": "cal_event_abc",
  "confirmation_message": "Tour booked for September 15 at 10 AM Central Time."
}
```

**Safety rule:** If this call fails, the voice agent must NOT tell the caller the
tour is booked. It must say: "I wasn't able to complete the booking. Someone from
our team will follow up to confirm your tour time."

**Idempotency:** If `call_id` + `slot_id` already exists, return the existing
booking (do not double-book).

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `SLOT_NO_LONGER_AVAILABLE` | Slot taken since availability check | No — offer new slots |
| `LEAD_NOT_FOUND` | lead_id invalid | No |
| `CALENDAR_WRITE_FAILED` | Calendar provider error | Yes (1 retry max) |
| `UPSTREAM_ERROR` | DB failure | Yes |

---

## Tool 9: `send_follow_up_email`

**When called:** Phase 4. After a successful booking, or at call end if the caller
provided a confirmed email address. Voice agent must confirm the email address with
the caller before calling this.

### Request

```
POST /v1/voice/emails
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "lead_id": "lead_uuid4",
  "template_type": "tour_confirmation",
  "recipient_email": "jordan.lee@email.com",
  "context": {
    "lead_name": "Jordan",
    "tour_date": "September 15",
    "tour_time": "10:00 AM",
    "tour_timezone": "Central Time",
    "property_name": "Maple Grove Apartments",
    "property_address": "123 Maple Ave, Austin, TX 78701"
  }
}
```

**Proposed template_type values:**

- `tour_confirmation` — after tour booked
- `general_followup` — info sent after non-booking leasing inquiry
- `maintenance_acknowledgment` — resident maintenance intake

**Safety rule:** Do not call this without `email_confirmed: true` in the call state.

### Response (200 or 202)

```json
{
  "email_id": "email_uuid4",
  "status": "queued",
  "accepted": true
}
```

202 is acceptable — email delivery is async.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `LEAD_NOT_FOUND` | lead_id invalid | No |
| `INVALID_TEMPLATE` | Unknown template_type | No |
| `EMAIL_PROVIDER_ERROR` | Delivery provider failure | Yes |
| `UPSTREAM_ERROR` | DB failure | Yes |

---

## Tool 10: `request_human_handoff`

**When called:** Any time the agent detects a Fair Housing question, legal/financial
advice request, emergency, low-confidence situation, backend tool failure, or explicit
caller request for a human. Also called if the booking or email workflow fails twice.

### Request

```
POST /v1/voice/calls/{call_id}/handoff
```

```json
{
  "property_id": "prop_abc123",
  "call_id": "call_uuid4",
  "reason": "fair_housing_question",
  "notes": "Caller asked about occupancy limits in a way that may relate to familial status.",
  "caller_phone_number": "REDACTED",
  "escalation_contact": "leasing_manager"
}
```

**Note:** `caller_phone_number` should be redacted in the log record but available
to the human receiving the handoff through the dashboard (Harsha to confirm field
handling). `escalation_contact` pulls from property profile's `escalation_contacts`.

**Valid reason values (maps to EscalationReason enum in voice agent):**

- `fair_housing_question`
- `legal_question`
- `financial_advice_requested`
- `eligibility_question`
- `emergency`
- `low_confidence`
- `caller_requested`
- `backend_tool_failure`
- `booking_failed`
- `unknown`

### Response (201)

```json
{
  "handoff_id": "handoff_uuid4",
  "accepted": true,
  "notification_sent": true
}
```

**Voice agent behavior after this call:** Say to the caller — "I'm connecting you
with our team. Someone will follow up with you shortly." Then end the LiveKit session.

### Error codes

| Code | Meaning | Retryable |
|------|---------|-----------|
| `CALL_NOT_FOUND` | call_id unknown | Yes |
| `UPSTREAM_ERROR` | DB/notification failure | Yes (attempt once more) |

---

## Open Questions for Harsha

1. **Path prefix:** Is `/v1/voice/` correct, or will endpoints be namespaced differently?
2. **Auth:** What auth header does the voice agent use? Service-to-service JWT, API key, or mTLS?
3. **Call record creation:** Does `call_started` event create the call record, or is there a separate `POST /v1/voice/calls` endpoint?
4. **`ai_summary` field in save_call_summary:** Does backend want the agent's Gemini-generated summary, or will it generate its own from the transcript?
5. **Segment idempotency:** Should the voice agent include its own `segment_id` in save_transcript_segment, or does backend always generate new IDs?
6. **Email `context` shape:** Confirm required fields for each template_type so voice agent can validate before calling.
7. **SLO enforcement:** If backend exceeds latency targets during a live call, should voice agent time out and continue (non-blocking) or block and retry?

---

## What Akhil Will Do Once Harsha Publishes Schemas

1. Read `services/api/app/schemas` (Pydantic models).
2. Update `voice_agent/tools/backend_client.py` to match exactly — no invented fields.
3. Remove any shape in this document that differs from Harsha's published schema.
4. Run contract tests against the staging endpoint.
