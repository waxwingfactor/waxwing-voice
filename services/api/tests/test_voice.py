"""Integration tests for voice tool endpoints.

Covers every item in services/api/tests/README.md minimum coverage list.

NOTE on graceful degradation (tests 8-10):
- POST /v1/voice/check-availability: when Google Calendar is not configured,
  the endpoint falls back to 3 hardcoded stub slots and returns 200 — it does
  NOT return 503. The test verifies the fallback shape.
- POST /v1/voice/book-tour: when Google Calendar is not configured, the booking
  record is still created and 200 is returned with calendar_event_id=null.
- POST /v1/voice/send-email: when SendGrid is not configured,
  send_sendgrid_email() returns False, delivery_status is set to "failed", and
  200 is returned — it does NOT return 502. The test verifies delivery_status.

These behaviors are by design — voice agent must never be blocked by an
unconfigured third-party integration.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.conftest import CALLER_PHONE, COMPANY_ID, PROPERTY_ID


# ---------------------------------------------------------------------------
# 1. Create or update lead
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_or_update_lead(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POST /v1/voice/leads returns 200 with a valid lead_id."""
    resp = await client.post(
        "/v1/voice/leads",
        json={
            "property_id": str(PROPERTY_ID),
            "call_id": str(call_id),
            "lead_fields": {
                "name": "Test Caller",
                "phone": CALLER_PHONE,
                "email": "testcaller@example.com",
                "desired_unit_type": "2BR",
                "budget": 2200.00,
                "tour_interest": True,
                "lead_score": "warm",
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["lead_id"])  # valid UUID, no ValueError
    assert data["property_id"] == str(PROPERTY_ID)
    assert data["call_id"] == str(call_id)
    assert isinstance(data["created"], bool)


# ---------------------------------------------------------------------------
# 2. Lead upsert is idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_upsert_is_idempotent(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POSTing the same (property_id, phone) twice returns the same lead_id."""
    payload = {
        "property_id": str(PROPERTY_ID),
        "call_id": str(call_id),
        "lead_fields": {
            "phone": CALLER_PHONE,
            "name": "Test Caller",
        },
    }
    resp1 = await client.post("/v1/voice/leads", json=payload, headers=auth_headers)
    resp2 = await client.post("/v1/voice/leads", json=payload, headers=auth_headers)

    assert resp1.status_code == 200, resp1.text
    assert resp2.status_code == 200, resp2.text

    lead_id_1 = resp1.json()["lead_id"]
    lead_id_2 = resp2.json()["lead_id"]
    assert lead_id_1 == lead_id_2, "Same phone should upsert to the same lead row"

    # Second call updated an existing row — created must be False
    assert resp2.json()["created"] is False


# ---------------------------------------------------------------------------
# 3. Lead with invalid property
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lead_invalid_property(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """Unknown property_id should return 404 PROPERTY_NOT_FOUND."""
    resp = await client.post(
        "/v1/voice/leads",
        json={
            "property_id": str(uuid.uuid4()),
            "call_id": str(call_id),
            "lead_fields": {"phone": "+15125550000"},
        },
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"


# ---------------------------------------------------------------------------
# 4. Create call event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_call_event(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POST /v1/voice/events returns 200 with a valid event_id."""
    resp = await client.post(
        "/v1/voice/events",
        json={
            "call_id": str(call_id),
            "event_type": "lead_captured",
            "payload": {"note": "caller asked about 2BR"},
            "occurred_at": datetime.now(UTC).isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["event_id"])
    assert data["call_id"] == str(call_id)


# ---------------------------------------------------------------------------
# 5. Save transcript segment
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_transcript_segment(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POST /v1/voice/transcript-segment returns 200 with a valid segment_id."""
    resp = await client.post(
        "/v1/voice/transcript-segment",
        json={
            "call_id": str(call_id),
            "speaker": "caller",
            "text": "Hi, I was wondering about your 2-bedroom availability.",
            "timestamp": 12.34,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["segment_id"])
    assert data["call_id"] == str(call_id)


# ---------------------------------------------------------------------------
# 6. Save call summary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_call_summary(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POST /v1/voice/call-summary returns 200 with summary_saved=True."""
    resp = await client.post(
        "/v1/voice/call-summary",
        json={
            "call_id": str(call_id),
            "summary": "Caller asked about 2BR availability and budget.",
            "primary_intent": "leasing_inquiry",
            "sentiment": "positive",
            "action_items": ["[TOUR] Follow up on tour availability"],
            "escalation_flag": False,
            "lead_fields_extracted": {"desired_unit_type": "2BR", "budget": 2200},
            "next_steps": "Send tour confirmation email.",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["call_id"] == str(call_id)
    assert data["summary_saved"] is True


# ---------------------------------------------------------------------------
# 7. Search knowledge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_knowledge(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """POST /v1/voice/search-knowledge returns 200 with a results list.

    Results may be empty — seed chunks use zero-vector embeddings which
    will not match cosine similarity queries unless OPENAI_API_KEY is set.
    We assert the response shape, not the content.
    """
    resp = await client.post(
        "/v1/voice/search-knowledge",
        json={
            "property_id": str(PROPERTY_ID),
            "query": "What is the pet policy?",
            "call_id": str(call_id),
            "top_k": 5,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["property_id"] == str(PROPERTY_ID)
    assert data["query"] == "What is the pet policy?"
    assert isinstance(data["results"], list)
    # If any results are returned, validate their shape
    for result in data["results"]:
        assert "chunk_text" in result
        assert "source_label" in result
        assert "similarity_score" in result
        assert 0.0 <= result["similarity_score"] <= 1.0


# ---------------------------------------------------------------------------
# 8. Check availability — fallback to stub slots (no Google Calendar configured)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_availability_calendar_not_configured(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """POST /v1/voice/check-availability returns 200 with stub slots.

    When Google Calendar credentials are absent, the endpoint falls back to
    3 hardcoded stub slots rather than returning an error. This is intentional:
    the voice agent must never be blocked by a missing integration.
    """
    resp = await client.post(
        "/v1/voice/check-availability",
        json={
            "property_id": str(PROPERTY_ID),
            "date_range": {
                "start_date": "2026-05-15",
                "end_date": "2026-05-16",
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["property_id"] == str(PROPERTY_ID)
    assert isinstance(data["available_slots"], list)
    # Stub fallback always produces exactly 3 slots
    assert len(data["available_slots"]) == 3
    # Each slot should have the expected fields
    for slot in data["available_slots"]:
        assert "date" in slot
        assert "start_time" in slot
        assert "end_time" in slot
        assert "slot_id" in slot
        # Stub slot IDs follow the pattern stub-{date}-{HH:MM}
        assert slot["slot_id"].startswith("stub-")


# ---------------------------------------------------------------------------
# 9. Book tour — calendar not configured (booking still created)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_book_tour_calendar_not_configured(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """POST /v1/voice/book-tour returns 200 even without Google Calendar.

    When Google Calendar is not configured, calendar_event_id will be null
    but the booking record is still created and the response is 200.
    This is the intended graceful-degradation behavior.
    """
    resp = await client.post(
        "/v1/voice/book-tour",
        json={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "selected_slot": {
                "date": "2026-05-20",
                "start_time": "10:00:00",
                "end_time": "10:30:00",
                "slot_id": "stub-2026-05-20-10:00",
            },
            "tour_type": "in_person",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["booking_id"])
    # Without Google Calendar credentials, calendar_event_id must be null
    assert data["calendar_event_id"] is None
    assert data["status"] == "confirmed"
    assert data["tour_date"] == "2026-05-20"


# ---------------------------------------------------------------------------
# 10. Send email — no SendGrid key configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_email_no_sendgrid(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """POST /v1/voice/send-email returns 200 with delivery_status='failed'.

    When SENDGRID_API_KEY is not set, send_sendgrid_email() returns False
    immediately. The EmailRecord is still created with delivery_status='failed'
    and the endpoint returns 200. The voice agent is responsible for
    communicating the failure to the caller.
    """
    resp = await client.post(
        "/v1/voice/send-email",
        json={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "template_type": "tour_confirmation",
            "context": {
                "tour_date": "May 20, 2026",
                "tour_time": "10:00 AM",
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["email_id"])
    # Lead has an email (set in lead_id fixture) — delivery attempted but failed
    # because SENDGRID_API_KEY is not configured in local .env
    assert data["delivery_status"] == "failed"
    assert "subject" in data
    assert "recipient" in data


# ---------------------------------------------------------------------------
# 11. Request handoff
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_handoff(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """POST /v1/voice/request-handoff returns 200 with notification_sent field."""
    resp = await client.post(
        "/v1/voice/request-handoff",
        json={
            "property_id": str(PROPERTY_ID),
            "call_id": str(call_id),
            "reason": "Caller requested to speak with a human agent.",
            "urgency": "medium",
            "lead_id": str(lead_id),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert uuid.UUID(data["handoff_id"])
    assert data["call_id"] == str(call_id)
    assert data["status"] == "requested"
    # Phase 1/4: notification_sent is always False until SMS/Slack is wired up
    assert "notification_sent" in data
    assert isinstance(data["notification_sent"], bool)
