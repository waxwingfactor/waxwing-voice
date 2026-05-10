"""Integration tests for /v1/bookings endpoints.

Bookings are created by the voice tool POST /v1/voice/book-tour. We reuse
that endpoint to seed test data, then verify list/detail/scope behavior on
the new dashboard-facing endpoints.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.auth import create_access_token
from tests.conftest import PROPERTY_ID


async def _create_booking(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
    *,
    tour_date: str = "2026-06-15",
    start_time: str = "10:00:00",
    end_time: str = "10:30:00",
    tour_type: str = "in_person",
) -> uuid.UUID:
    """Create a booking via the voice tool and return its id."""
    resp = await client.post(
        "/v1/voice/book-tour",
        json={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "selected_slot": {
                "date": tour_date,
                "start_time": start_time,
                "end_time": end_time,
                "slot_id": f"stub-{tour_date}-{start_time[:5]}",
            },
            "tour_type": tour_type,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["booking_id"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bookings_list_returns_paginated_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """GET /v1/bookings/?property_id=X returns a paginated envelope."""
    booking_id = await _create_booking(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/bookings/",
        params={"property_id": str(PROPERTY_ID), "page": 1, "page_size": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data
    assert "total" in data
    assert data["page"] == 1
    assert data["page_size"] == 50
    assert data["total"] >= 1

    ids = [item["id"] for item in data["items"]]
    assert str(booking_id) in ids

    # Item shape sanity check
    sample = next(item for item in data["items"] if item["id"] == str(booking_id))
    for key in (
        "id",
        "lead_id",
        "property_id",
        "tour_date",
        "start_time",
        "tour_type",
        "status",
        "confirmation_email_status",
        "created_at",
    ):
        assert key in sample


@pytest.mark.asyncio
async def test_bookings_list_filters_by_status(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`status=confirmed` returns only confirmed bookings."""
    await _create_booking(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/bookings/",
        params={"property_id": str(PROPERTY_ID), "status": "confirmed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["status"] == "confirmed"

    # Bogus status returns an empty page (no error)
    resp_zero = await client.get(
        "/v1/bookings/",
        params={"property_id": str(PROPERTY_ID), "status": "no_such_status"},
        headers=auth_headers,
    )
    assert resp_zero.status_code == 200
    assert resp_zero.json()["total"] == 0


@pytest.mark.asyncio
async def test_bookings_list_filters_by_lead_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`lead_id` filter returns only bookings for that lead."""
    booking_id = await _create_booking(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/bookings/",
        params={"property_id": str(PROPERTY_ID), "lead_id": str(lead_id)},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["lead_id"] == str(lead_id)
    assert str(booking_id) in [item["id"] for item in data["items"]]


@pytest.mark.asyncio
async def test_bookings_list_filters_by_date_range(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`date_from` / `date_to` filter on tour_date."""
    target_date = "2026-07-22"
    booking_id = await _create_booking(
        client, auth_headers, call_id, lead_id, tour_date=target_date
    )

    resp = await client.get(
        "/v1/bookings/",
        params={
            "property_id": str(PROPERTY_ID),
            "date_from": target_date,
            "date_to": target_date,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    ids = [item["id"] for item in data["items"]]
    assert str(booking_id) in ids
    for item in data["items"]:
        assert item["tour_date"] == target_date


@pytest.mark.asyncio
async def test_bookings_list_invalid_date_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Bad date_from format returns 400 INVALID_REQUEST."""
    resp = await client.get(
        "/v1/bookings/",
        params={"property_id": str(PROPERTY_ID), "date_from": "06-15-2026"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_bookings_list_unknown_property_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/bookings/",
        params={"property_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"


# ---------------------------------------------------------------------------
# Detail
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booking_detail_returns_full_record(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """GET /v1/bookings/{id} returns the full record."""
    booking_id = await _create_booking(client, auth_headers, call_id, lead_id)

    resp = await client.get(f"/v1/bookings/{booking_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(booking_id)
    assert data["lead_id"] == str(lead_id)
    assert data["property_id"] == str(PROPERTY_ID)
    assert data["calendar_provider"] == "google_calendar"
    assert "calendar_event_id" in data
    assert "tour_date" in data
    assert "start_time" in data
    assert "end_time" in data
    assert "status" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_booking_detail_unknown_id_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown booking_id returns 404 BOOKING_NOT_FOUND."""
    resp = await client.get(f"/v1/bookings/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "BOOKING_NOT_FOUND"
    assert body["error"]["retryable"] is False


# ---------------------------------------------------------------------------
# Multi-tenant scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_booking_detail_other_company_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """A booking belonging to another company returns 404, not 403."""
    booking_id = await _create_booking(client, auth_headers, call_id, lead_id)

    # Mint a token for a totally different company
    other_token = create_access_token(uuid.uuid4())
    other_headers = {"Authorization": f"Bearer {other_token}"}

    resp = await client.get(f"/v1/bookings/{booking_id}", headers=other_headers)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "BOOKING_NOT_FOUND"
