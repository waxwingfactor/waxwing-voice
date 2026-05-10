"""Integration tests for /v1/calls endpoints — focuses on text search.

These tests exercise the `q` substring search across `caller_phone` and
`summary` introduced for Alex's dashboard search bar. Other call lifecycle
behaviors (create / update / detail) are covered indirectly by test_voice.py
fixtures.
"""

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from tests.conftest import PROPERTY_ID


async def _create_call(
    client: AsyncClient,
    auth_headers: dict[str, str],
    caller_phone: str,
) -> uuid.UUID:
    """Create a fresh call with the given caller_phone; return its id."""
    resp = await client.post(
        "/v1/calls/",
        json={
            "property_id": str(PROPERTY_ID),
            "twilio_call_sid": str(uuid.uuid4()),
            "caller_phone": caller_phone,
            "started_at": datetime.now(UTC).isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


async def _set_call_summary(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    summary: str,
) -> None:
    """Use the voice tool endpoint to populate the call summary."""
    resp = await client.post(
        "/v1/voice/call-summary",
        json={
            "call_id": str(call_id),
            "summary": summary,
            "primary_intent": "leasing_inquiry",
            "sentiment": "positive",
            "action_items": [],
            "escalation_flag": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# Text search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calls_text_search_matches_caller_phone(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`q` matches a substring of caller_phone (case-insensitive)."""
    # Use a unique phone fragment so we can assert the result set
    unique_fragment = uuid.uuid4().hex[:8]
    target_phone = f"+1512999{unique_fragment[:4]}"
    target_id = await _create_call(client, auth_headers, target_phone)

    resp = await client.get(
        "/v1/calls/",
        params={"property_id": str(PROPERTY_ID), "q": unique_fragment[:4]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(target_id) in ids


@pytest.mark.asyncio
async def test_calls_text_search_matches_summary(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """`q` matches a substring of summary (case-insensitive)."""
    target_phone = f"+1512888{uuid.uuid4().hex[:4]}"
    target_id = await _create_call(client, auth_headers, target_phone)

    unique_token = f"WAXWINGSEARCH{uuid.uuid4().hex[:8].upper()}"
    summary = f"Caller asked about pricing. {unique_token} appears here."
    await _set_call_summary(client, auth_headers, target_id, summary)

    # Lowercase query should still match (case-insensitive ilike)
    resp = await client.get(
        "/v1/calls/",
        params={"property_id": str(PROPERTY_ID), "q": unique_token.lower()},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(target_id) in ids
    assert data["total"] >= 1


@pytest.mark.asyncio
async def test_calls_text_search_empty_returns_all(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """When `q` is omitted the endpoint behaves like the unfiltered list."""
    # Ensure at least one call exists
    await _create_call(client, auth_headers, "+15125551111")

    resp_no_q = await client.get(
        "/v1/calls/",
        params={"property_id": str(PROPERTY_ID)},
        headers=auth_headers,
    )
    assert resp_no_q.status_code == 200, resp_no_q.text
    total_no_q = resp_no_q.json()["total"]

    # Empty-string q should also be a no-op (whitespace-only treated as None)
    resp_empty = await client.get(
        "/v1/calls/",
        params={"property_id": str(PROPERTY_ID), "q": "   "},
        headers=auth_headers,
    )
    assert resp_empty.status_code == 200, resp_empty.text
    assert resp_empty.json()["total"] == total_no_q
    assert total_no_q >= 1


# ---------------------------------------------------------------------------
# Multi-tenant scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_calls_list_unknown_property_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/calls/",
        params={"property_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_calls_list_invalid_date_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Bad date_from format returns 400 INVALID_REQUEST."""
    resp = await client.get(
        "/v1/calls/",
        params={"property_id": str(PROPERTY_ID), "date_from": "not-a-date"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
