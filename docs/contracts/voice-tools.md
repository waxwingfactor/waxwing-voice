# Waxwing Voice Tool Contracts

Owner: Harsha (backend)
Consumer: Akhil (voice agent)
Source of truth: `services/api/app/schemas/voice_tools.py`

All tools are HTTP POST endpoints under `/v1/voice/`. Auth: `X-Company-Id` header (Phase 0/1 temporary; replaced by JWT in Phase 5).

Error shape used by all endpoints:
```json
{"error": {"code": "PROPERTY_NOT_FOUND", "message": "...", "retryable": false}}
```

---

## 1. search_property_knowledge

**POST** `/v1/voice/search-knowledge`

**Request**
```json
{
  "property_id": "uuid",
  "query": "What is the pet policy for large dogs?",
  "call_id": "uuid",
  "top_k": 5
}
```

**Response**
```json
{
  "property_id": "uuid",
  "query": "What is the pet policy for large dogs?",
  "results": [
    {
      "chunk_text": "Pet Policy: Pets are welcome...",
      "source_label": "Leasing Policies & Pet Rules, p.2",
      "page_number": null,
      "similarity_score": 0.91
    }
  ]
}
```

**Errors**: `PROPERTY_NOT_FOUND` (404, retryable: false)

**Notes**: Returns empty `results` list (not 404) when no relevant chunks exist. Always scoped to `property_id`. Timeout: 2 seconds.

---

## 2. get_property_profile

**GET** `/v1/properties/{property_id}`

**Response**
```json
{
  "id": "uuid",
  "name": "Sunset Apartments",
  "address": "123 Sunset Blvd, Austin, TX 78701",
  "amenities": {"pool": true, "gym": true},
  "office_hours": {"mon_fri": "9am-6pm"},
  "leasing_policies": "12-month minimum lease...",
  "maintenance_instructions": "Submit via resident portal...",
  "escalation_contacts": [{"name": "Jane Smith", "phone": "+15125550100", "role": "Property Manager"}],
  "call_handling_rules": {"after_hours_message": "..."}
}
```

**Errors**: `PROPERTY_NOT_FOUND` (404, retryable: false)

---

## 3. create_or_update_lead

**POST** `/v1/voice/leads`

Upserts on `(property_id, phone)`. Send the same payload multiple times safely.

**Request**
```json
{
  "property_id": "uuid",
  "call_id": "uuid",
  "lead_fields": {
    "name": "Maria Garcia",
    "phone": "+15125550199",
    "email": "maria@example.com",
    "desired_unit_type": "2BR",
    "move_in_date": "2026-07-01",
    "budget": 2200.00,
    "tour_interest": true,
    "lead_score": "hot"
  }
}
```

**lead_score values**: `hot` | `warm` | `cold`
**urgency values**: `low` | `medium` | `high` | `immediate`

**Response**
```json
{"lead_id": "uuid", "property_id": "uuid", "call_id": "uuid", "created": true}
```

**Errors**: `PROPERTY_NOT_FOUND` (404), `INVALID_REQUEST` (422)

---

## 4. create_call_event

**POST** `/v1/voice/events`

**event_type values** _(NEEDS AKHIL REVIEW — confirm before Phase 1)_:
`call_started` | `call_ended` | `lead_captured` | `tour_booked` | `email_sent` | `escalated` | `knowledge_retrieved` | `tool_called` | `tool_failed` | `interruption_detected` | `silence_detected`

**Request**
```json
{
  "call_id": "uuid",
  "event_type": "lead_captured",
  "payload": {"lead_id": "uuid"},
  "occurred_at": "2026-05-09T14:32:00Z"
}
```

**Response**: `{"event_id": "uuid", "call_id": "uuid"}`

**Notes**: Pure durable write — no side effects. Idempotent per `event_id`.

---

## 5. save_transcript_segment

**POST** `/v1/voice/transcript-segment`

**speaker values**: `agent` | `caller`

**Request**
```json
{
  "call_id": "uuid",
  "speaker": "caller",
  "text": "Hi, I was wondering about your 2-bedroom availability.",
  "timestamp": 12.34
}
```

**Response**: `{"segment_id": "uuid", "call_id": "uuid"}`

**Notes**: High-frequency endpoint — called after every STT segment. Validated at 120 req/min per call (Phase 5).

---

## 6. save_call_summary

**POST** `/v1/voice/call-summary`

**sentiment values**: `positive` | `neutral` | `negative` | `frustrated`

**Request**
```json
{
  "call_id": "uuid",
  "summary": "Maria called to ask about 2BR availability...",
  "primary_intent": "leasing_inquiry",
  "sentiment": "positive",
  "action_items": ["[TOUR] Confirm tour booking for May 15"],
  "escalation_flag": false,
  "lead_fields_extracted": {"name": "Maria Garcia", "move_in_date": "2026-07-01"},
  "next_steps": "Send tour confirmation email."
}
```

**Response**: `{"call_id": "uuid", "summary_saved": true}`

---

## 7. check_tour_availability

**POST** `/v1/voice/check-availability`

**Request**
```json
{
  "property_id": "uuid",
  "date_range": {"start_date": "2026-05-12", "end_date": "2026-05-16"}
}
```

**Response**
```json
{
  "property_id": "uuid",
  "available_slots": [
    {"date": "2026-05-15", "start_time": "10:00:00", "end_time": "10:30:00", "slot_id": "gcal-abc123"}
  ]
}
```

**Errors**: `CALENDAR_UNAVAILABLE` (503, retryable: true) if Google Calendar is unreachable.

**Notes**: `slot_id` is stable for the duration of the call. 3-second timeout.

---

## 8. book_tour

**POST** `/v1/voice/book-tour`

**tour_type values**: `in_person` | `self_guided` | `virtual`

**Request**
```json
{
  "property_id": "uuid",
  "lead_id": "uuid",
  "call_id": "uuid",
  "selected_slot": {"date": "2026-05-15", "start_time": "10:00:00", "end_time": "10:30:00", "slot_id": "gcal-abc123"},
  "tour_type": "in_person"
}
```

**Response**
```json
{"booking_id": "uuid", "calendar_event_id": "gcal-event-xyz", "tour_date": "2026-05-15", "start_time": "10:00:00", "status": "confirmed"}
```

**Errors**: `BOOKING_SLOT_UNAVAILABLE` (409, retryable: false), `CALENDAR_UNAVAILABLE` (503, retryable: true), `LEAD_NOT_FOUND` (404, retryable: false)

---

## 9. send_follow_up_email

**POST** `/v1/voice/send-email`

**template_type values**: `tour_confirmation` | `follow_up` | `lead_response` | `general`

**Request**
```json
{
  "property_id": "uuid",
  "lead_id": "uuid",
  "call_id": "uuid",
  "template_type": "tour_confirmation",
  "context": {
    "lead_name": "Maria Garcia",
    "tour_date": "May 15, 2026",
    "tour_time": "10:00 AM",
    "property_name": "Sunset Apartments",
    "property_address": "123 Sunset Blvd, Austin, TX 78701"
  }
}
```

**Response**: `{"email_id": "uuid", "recipient": "maria@example.com", "subject": "...", "delivery_status": "sent"}`

**Errors**: `EMAIL_DELIVERY_FAILED` (502, retryable: true), `LEAD_NOT_FOUND` (404, retryable: false)

---

## 10. request_human_handoff

**POST** `/v1/voice/request-handoff`

**urgency values**: `low` | `medium` | `high` | `emergency`

**Request**
```json
{
  "property_id": "uuid",
  "call_id": "uuid",
  "reason": "Caller mentioned attorney involvement.",
  "urgency": "high",
  "lead_id": "uuid"
}
```

**Response**: `{"handoff_id": "uuid", "call_id": "uuid", "status": "requested", "notification_sent": true}`
