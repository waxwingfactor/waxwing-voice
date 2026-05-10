"""Integration tests for /v1/leads endpoints — focuses on text search.

Other lead lifecycle behaviors (create / detail) are covered indirectly by
test_voice.py fixtures that exercise create_or_update_lead.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import PROPERTY_ID


async def _upsert_lead(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    *,
    name: str | None = None,
    phone: str,
    email: str | None = None,
) -> uuid.UUID:
    """Upsert a lead via the voice tool endpoint and return its id."""
    fields: dict[str, object] = {"phone": phone}
    if name is not None:
        fields["name"] = name
    if email is not None:
        fields["email"] = email
    resp = await client.post(
        "/v1/voice/leads",
        json={
            "property_id": str(PROPERTY_ID),
            "call_id": str(call_id),
            "lead_fields": fields,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["lead_id"])


# ---------------------------------------------------------------------------
# Text search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leads_text_search_matches_name(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """`q` matches a substring of name (case-insensitive)."""
    unique_phone = f"+1512777{uuid.uuid4().hex[:4]}"
    unique_token = f"Zephyr{uuid.uuid4().hex[:6]}"
    target_id = await _upsert_lead(
        client,
        auth_headers,
        call_id,
        name=f"{unique_token} Doe",
        phone=unique_phone,
    )

    # Lowercase query should match — ilike is case-insensitive
    resp = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID), "q": unique_token.lower()},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    ids = [item["id"] for item in data["items"]]
    assert str(target_id) in ids


@pytest.mark.asyncio
async def test_leads_text_search_matches_phone(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """`q` matches a substring of phone."""
    fragment = uuid.uuid4().hex[:6]
    unique_phone = f"+1512666{fragment[:4]}"
    target_id = await _upsert_lead(
        client,
        auth_headers,
        call_id,
        phone=unique_phone,
    )

    resp = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID), "q": fragment[:4]},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(target_id) in ids


@pytest.mark.asyncio
async def test_leads_text_search_matches_email(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> None:
    """`q` matches a substring of email."""
    fragment = uuid.uuid4().hex[:8]
    unique_phone = f"+1512555{uuid.uuid4().hex[:4]}"
    target_id = await _upsert_lead(
        client,
        auth_headers,
        call_id,
        phone=unique_phone,
        email=f"{fragment}@example.org",
    )

    resp = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID), "q": fragment},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert str(target_id) in ids


@pytest.mark.asyncio
async def test_leads_text_search_empty_returns_all(
    client: AsyncClient,
    auth_headers: dict[str, str],
    lead_id: uuid.UUID,
) -> None:
    """When `q` is omitted the endpoint behaves like the unfiltered list."""
    resp_no_q = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID)},
        headers=auth_headers,
    )
    assert resp_no_q.status_code == 200, resp_no_q.text
    total_no_q = resp_no_q.json()["total"]
    assert total_no_q >= 1

    # Whitespace-only `q` should be treated as None
    resp_empty = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID), "q": "  "},
        headers=auth_headers,
    )
    assert resp_empty.status_code == 200, resp_empty.text
    assert resp_empty.json()["total"] == total_no_q


# ---------------------------------------------------------------------------
# Multi-tenant scope and validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_leads_list_unknown_property_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/leads/",
        params={"property_id": str(uuid.uuid4())},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_leads_list_invalid_date_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Bad date_to format returns 400 INVALID_REQUEST."""
    resp = await client.get(
        "/v1/leads/",
        params={"property_id": str(PROPERTY_ID), "date_to": "2026/01/01"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"
