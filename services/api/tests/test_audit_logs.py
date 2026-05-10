"""Integration tests for /v1/audit-logs endpoint.

Audit log rows are written as a side effect of voice tool calls
(book-tour -> TOUR_BOOKED, send-email -> EMAIL_SENT, request-handoff ->
HANDOFF_REQUESTED). We exercise those endpoints to seed test data.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.auth import create_access_token
from tests.conftest import PROPERTY_ID


async def _book_tour(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Trigger a TOUR_BOOKED audit log entry."""
    resp = await client.post(
        "/v1/voice/book-tour",
        json={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "selected_slot": {
                "date": "2026-08-10",
                "start_time": "11:00:00",
                "end_time": "11:30:00",
                "slot_id": "stub-2026-08-10-11:00",
            },
            "tour_type": "in_person",
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


async def _request_handoff(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Trigger a HANDOFF_REQUESTED audit log entry."""
    resp = await client.post(
        "/v1/voice/request-handoff",
        json={
            "property_id": str(PROPERTY_ID),
            "call_id": str(call_id),
            "reason": "Caller asked for a human.",
            "urgency": "medium",
            "lead_id": str(lead_id),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_logs_list_returns_paginated_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """GET /v1/audit-logs/?property_id=X returns a paginated envelope."""
    await _book_tour(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID), "page": 1, "page_size": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 50
    assert data["total"] >= 1

    # Item shape sanity check
    sample = data["items"][0]
    for key in (
        "id",
        "company_id",
        "property_id",
        "actor_type",
        "actor_id",
        "action",
        "entity_type",
        "entity_id",
        "metadata",
        "created_at",
    ):
        assert key in sample


@pytest.mark.asyncio
async def test_audit_logs_filter_by_action(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`action=TOUR_BOOKED` returns only TOUR_BOOKED rows."""
    await _book_tour(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID), "action": "TOUR_BOOKED"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["action"] == "TOUR_BOOKED"


@pytest.mark.asyncio
async def test_audit_logs_filter_by_entity_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`entity_type=booking` returns only booking-related rows."""
    await _book_tour(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID), "entity_type": "booking"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["entity_type"] == "booking"


@pytest.mark.asyncio
async def test_audit_logs_filter_by_actor_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`actor_type=VOICE_AGENT` returns only voice-agent rows."""
    await _request_handoff(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID), "actor_type": "VOICE_AGENT"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["actor_type"] == "VOICE_AGENT"


@pytest.mark.asyncio
async def test_audit_logs_date_range_filter(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """A date range that excludes today returns 0 rows; today returns >= 1."""
    await _book_tour(client, auth_headers, call_id, lead_id)

    # Far-past date range — should be empty
    resp_empty = await client.get(
        "/v1/audit-logs/",
        params={
            "property_id": str(PROPERTY_ID),
            "date_from": "2000-01-01",
            "date_to": "2000-01-02",
        },
        headers=auth_headers,
    )
    assert resp_empty.status_code == 200, resp_empty.text
    assert resp_empty.json()["total"] == 0

    # Wide range covering today — should match
    resp_wide = await client.get(
        "/v1/audit-logs/",
        params={
            "property_id": str(PROPERTY_ID),
            "date_from": "2020-01-01",
            "date_to": "2099-12-31",
        },
        headers=auth_headers,
    )
    assert resp_wide.status_code == 200, resp_wide.text
    assert resp_wide.json()["total"] >= 1


@pytest.mark.asyncio
async def test_audit_logs_invalid_date_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Bad date_to format returns 400 INVALID_REQUEST."""
    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID), "date_to": "tomorrow"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_audit_logs_unknown_property_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"


# ---------------------------------------------------------------------------
# Multi-tenant scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_logs_other_company_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """A property belonging to another company returns 404 PROPERTY_NOT_FOUND."""
    await _book_tour(client, auth_headers, call_id, lead_id)

    other_token = create_access_token(uuid.uuid4())
    other_headers = {"Authorization": f"Bearer {other_token}"}

    resp = await client.get(
        "/v1/audit-logs/",
        params={"property_id": str(PROPERTY_ID)},
        headers=other_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"
