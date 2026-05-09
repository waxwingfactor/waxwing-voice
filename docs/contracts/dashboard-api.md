# Waxwing Dashboard API Contracts

Owner: Harsha (backend)
Consumer: Alex (frontend dashboard)
Source of truth: `services/api/app/schemas/`

Base URL: `http://localhost:8000` (local), `https://api.staging.waxwing.example` (staging)
API version prefix: `/v1/`
Auth: `X-Company-Id` header (Phase 0/1 temporary; JWT in Phase 5)

All list endpoints return the same pagination envelope:
```json
{"items": [...], "total": 42, "page": 1, "page_size": 20}
```

Default page size: 20. Max: 100. Query params: `page` (int, default 1), `page_size` (int, default 20).

Error shape:
```json
{"error": {"code": "PROPERTY_NOT_FOUND", "message": "...", "retryable": false}}
```

---

## Properties

### GET /v1/properties/
Returns all properties for the authenticated company.

**Response item**
```json
{"id": "uuid", "company_id": "uuid", "name": "Sunset Apartments", "address": "123 Sunset Blvd, Austin, TX 78701", "created_at": "2026-05-09T00:00:00Z"}
```

### GET /v1/properties/{property_id}
Full property profile.

**Response** — see seed_property.json → `sample_api_responses["GET /v1/properties/..."]`

### GET /v1/properties/{property_id}/summary?date=2026-05-09
Aggregated dashboard metrics for the home tiles.

**Response**
```json
{
  "property_id": "uuid", "property_name": "Sunset Apartments", "date": "2026-05-09",
  "calls_today": 12, "new_leads_today": 4, "tours_booked_today": 2,
  "escalations_today": 1, "follow_ups_sent_today": 3, "open_action_items": 5
}
```

---

## Calls

### GET /v1/calls/
**Query params**: `property_id` (required), `status`, `date_from` (YYYY-MM-DD), `date_to` (YYYY-MM-DD)

**Response item**
```json
{
  "id": "uuid", "property_id": "uuid", "caller_phone": "+15125550199",
  "started_at": "2026-05-09T14:30:00Z", "ended_at": "2026-05-09T14:42:00Z",
  "duration": 720, "status": "completed", "primary_intent": "leasing_inquiry",
  "sentiment": "positive", "escalation_status": "none", "escalation_flag": false,
  "created_at": "2026-05-09T14:30:00Z"
}
```

**call status values**: `active` | `completed` | `failed` | `abandoned`
**escalation_status values**: `none` | `requested` | `escalated`

### GET /v1/calls/{call_id}
Full call detail including transcript segments and events. `summary` and `lead_id` are null until Phase 2.

---

## Leads

_Available in Phase 2_

### GET /v1/leads/
**Query params**: `property_id` (required), `lead_status`, `date_from`, `date_to`, `tour_interest` (bool)

**lead_status values**: `new` | `contacted` | `toured` | `applied` | `closed` | `lost`
**lead_score values**: `hot` | `warm` | `cold`

### GET /v1/leads/{lead_id}
Full lead record.

---

## Documents

_Available in Phase 3_

### GET /v1/documents/
**Query params**: `property_id` (required)

**processing_status values**: `pending` | `processing` | `indexing` | `indexed` | `failed` | `retrying`

### POST /v1/documents/upload
Multipart form: `file` (PDF/DOCX/TXT, max 50MB), `property_id`

### POST /v1/documents/{document_id}/reindex
Returns `202 Accepted` — processing is async.

---

## Bookings

_Available in Phase 4_

**status values**: `confirmed` | `cancelled` | `rescheduled` | `no_show` | `completed`
**confirmation_email_status values**: `pending` | `sent` | `failed`

---

## Emails

_Available in Phase 4_

**delivery_status values**: `pending` | `sent` | `delivered` | `bounced` | `failed`
**template_type values**: `tour_confirmation` | `follow_up` | `lead_response` | `general`

---

## Notes for Alex

1. **Mock with seed_property.json**: Until backend endpoints are ready, use `tests/fixtures/seed_property.json` for static mock data. The UUIDs there match what the seed script inserts.
2. **NEEDS ALEX REVIEW fields**: Search for `# NEEDS ALEX REVIEW` in schema files — these are fields where filter/sort requirements need confirmation.
3. **Pagination**: All list endpoints return `{items, total, page, page_size}`. Never assume the API returns all records — always render a "load more" or page control.
4. **Null fields**: `summary`, `lead_id` on Call detail are null in Phase 1. Build null-safe UI states for these.
