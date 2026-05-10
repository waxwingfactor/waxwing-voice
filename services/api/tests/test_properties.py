"""Property profile endpoint integration tests.

Verifies GET / PATCH /v1/properties/{property_id} for both the happy path
and the 404 / validation cases. Seed data must be loaded before running.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog
from tests.conftest import COMPANY_ID, PROPERTY_ID


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


# ---------------------------------------------------------------------------
# PATCH /v1/properties/{property_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_property_partial_update(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH with a single field updates only that field and returns the full row."""
    # Read current state so we can restore-and-verify
    initial = await client.get(f"/v1/properties/{PROPERTY_ID}", headers=auth_headers)
    assert initial.status_code == 200, initial.text
    initial_data = initial.json()
    original_name = initial_data["name"]
    original_address = initial_data["address"]

    new_description = "Updated by PATCH test — partial update."
    resp = await client.patch(
        f"/v1/properties/{PROPERTY_ID}",
        json={"description": new_description},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(PROPERTY_ID)
    # Only description changed
    assert data["description"] == new_description
    assert data["name"] == original_name
    assert data["address"] == original_address


@pytest.mark.asyncio
async def test_patch_property_full_update(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH with multiple fields applies all of them in one request."""
    payload = {
        "name": "Sunset Apartments — PATCHED",
        "address": "999 Patched Way, Austin, TX",
        "description": "Full multi-field PATCH test",
        "amenities": {"pool": True, "patched": True},
        "office_hours": {"mon_fri": "8-8"},
        "leasing_policies": "Updated leasing policy text.",
        "maintenance_instructions": "Email maint@example.com.",
        "escalation_contacts": [
            {"name": "PATCH Contact", "phone": "+10000000000", "role": "Test"},
        ],
        "business_hour_rules": {"timezone": "UTC"},
        "call_handling_rules": {"escalate_after_seconds": 60},
    }
    resp = await client.patch(
        f"/v1/properties/{PROPERTY_ID}",
        json=payload,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    for key, expected in payload.items():
        assert data[key] == expected, f"field {key!r} mismatch: {data[key]!r} != {expected!r}"


@pytest.mark.asyncio
async def test_patch_property_wrong_company_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH on an unknown id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.patch(
        f"/v1/properties/{uuid.uuid4()}",
        json={"name": "ignored"},
        headers=auth_headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "PROPERTY_NOT_FOUND"


@pytest.mark.asyncio
async def test_patch_property_invalid_payload_returns_422(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """PATCH with the wrong type for a field returns 422 from FastAPI validation."""
    resp = await client.patch(
        f"/v1/properties/{PROPERTY_ID}",
        json={"amenities": "this should be a dict, not a string"},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_patch_property_writes_audit_log(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """A successful PATCH writes a PROPERTY_UPDATED audit log row with fields_changed."""
    payload = {"description": "Audit-log verification description."}
    resp = await client.patch(
        f"/v1/properties/{PROPERTY_ID}",
        json=payload,
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AuditLog)
            .where(
                AuditLog.company_id == COMPANY_ID,
                AuditLog.action == "PROPERTY_UPDATED",
                AuditLog.entity_id == str(PROPERTY_ID),
            )
            .order_by(AuditLog.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        assert row is not None, "Expected at least one PROPERTY_UPDATED audit log row"
        assert row.entity_type == "property"
        assert row.metadata_ is not None
        assert "fields_changed" in row.metadata_
        assert "description" in row.metadata_["fields_changed"]
