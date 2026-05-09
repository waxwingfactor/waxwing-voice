"""Property profile endpoint integration tests.

Verifies GET /v1/properties/{property_id} for both the happy path
and the 404 case. Seed data must be loaded before running.
"""

import uuid

import pytest
from httpx import AsyncClient

from tests.conftest import PROPERTY_ID


@pytest.mark.asyncio
async def test_get_property_profile(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /v1/properties/{PROPERTY_ID} returns 200 with expected fields."""
    resp = await client.get(f"/v1/properties/{PROPERTY_ID}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(PROPERTY_ID)
    assert "name" in data
    assert "amenities" in data
    assert "office_hours" in data
    assert "leasing_policies" in data
    assert "escalation_contacts" in data
    assert "call_handling_rules" in data


@pytest.mark.asyncio
async def test_get_property_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """GET /v1/properties/{unknown_id} returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(f"/v1/properties/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "PROPERTY_NOT_FOUND"
    assert body["error"]["retryable"] is False
