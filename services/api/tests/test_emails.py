"""Integration tests for /v1/emails endpoints.

Email records are produced by the voice tool POST /v1/voice/send-email. We
reuse it to seed test data; SendGrid is not configured in local .env so
delivery_status will be "failed" but the EmailRecord is created either way.
"""

import uuid

import pytest
from httpx import AsyncClient

from app.auth import create_access_token
from tests.conftest import PROPERTY_ID


async def _send_email(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
    *,
    template_type: str = "tour_confirmation",
) -> uuid.UUID:
    """Send a test email via the voice tool and return its id."""
    resp = await client.post(
        "/v1/voice/send-email",
        json={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
            "template_type": template_type,
            "context": {
                "tour_date": "May 20, 2026",
                "tour_time": "10:00 AM",
            },
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return uuid.UUID(resp.json()["email_id"])


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_emails_list_returns_paginated_results(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """GET /v1/emails/?property_id=X returns a paginated envelope."""
    email_id = await _send_email(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/emails/",
        params={"property_id": str(PROPERTY_ID), "page": 1, "page_size": 50},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["page"] == 1
    assert data["page_size"] == 50
    assert data["total"] >= 1

    ids = [item["id"] for item in data["items"]]
    assert str(email_id) in ids

    sample = next(item for item in data["items"] if item["id"] == str(email_id))
    for key in (
        "id",
        "lead_id",
        "call_id",
        "recipient",
        "subject",
        "template_type",
        "delivery_status",
        "sent_at",
        "created_at",
    ):
        assert key in sample
    # List item must NOT leak the body
    assert "body" not in sample


@pytest.mark.asyncio
async def test_emails_list_filters_by_template_type(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`template_type=tour_confirmation` returns only matching rows."""
    await _send_email(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/emails/",
        params={"property_id": str(PROPERTY_ID), "template_type": "tour_confirmation"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["template_type"] == "tour_confirmation"

    resp_other = await client.get(
        "/v1/emails/",
        params={"property_id": str(PROPERTY_ID), "template_type": "no_such_template"},
        headers=auth_headers,
    )
    assert resp_other.status_code == 200
    assert resp_other.json()["total"] == 0


@pytest.mark.asyncio
async def test_emails_list_filters_by_delivery_status(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """`delivery_status=failed` returns only failed rows.

    Without SENDGRID_API_KEY set, send-email always lands as 'failed'.
    """
    await _send_email(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/emails/",
        params={"property_id": str(PROPERTY_ID), "delivery_status": "failed"},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        assert item["delivery_status"] == "failed"


@pytest.mark.asyncio
async def test_emails_list_filters_by_lead_and_call(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """Combined `lead_id` + `call_id` filters narrow the result set."""
    email_id = await _send_email(client, auth_headers, call_id, lead_id)

    resp = await client.get(
        "/v1/emails/",
        params={
            "property_id": str(PROPERTY_ID),
            "lead_id": str(lead_id),
            "call_id": str(call_id),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] >= 1
    for item in data["items"]:
        assert item["lead_id"] == str(lead_id)
        assert item["call_id"] == str(call_id)
    assert str(email_id) in [item["id"] for item in data["items"]]


@pytest.mark.asyncio
async def test_emails_list_invalid_date_returns_400(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Bad date_from format returns 400 INVALID_REQUEST."""
    resp = await client.get(
        "/v1/emails/",
        params={"property_id": str(PROPERTY_ID), "date_from": "yesterday"},
        headers=auth_headers,
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["error"]["code"] == "INVALID_REQUEST"


@pytest.mark.asyncio
async def test_emails_list_unknown_property_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown property_id returns 404 PROPERTY_NOT_FOUND."""
    resp = await client.get(
        "/v1/emails/",
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
async def test_email_detail_returns_full_record(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """GET /v1/emails/{id} returns the full record including body."""
    email_id = await _send_email(client, auth_headers, call_id, lead_id)

    resp = await client.get(f"/v1/emails/{email_id}", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == str(email_id)
    assert data["property_id"] == str(PROPERTY_ID)
    assert data["lead_id"] == str(lead_id)
    assert data["call_id"] == str(call_id)
    assert "subject" in data
    assert "body" in data
    assert "delivery_status" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_email_detail_unknown_id_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    """Unknown email_id returns 404 EMAIL_NOT_FOUND."""
    resp = await client.get(f"/v1/emails/{uuid.uuid4()}", headers=auth_headers)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "EMAIL_NOT_FOUND"
    assert body["error"]["retryable"] is False


# ---------------------------------------------------------------------------
# Multi-tenant scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_email_detail_other_company_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
    lead_id: uuid.UUID,
) -> None:
    """An email belonging to another company returns 404, not 403."""
    email_id = await _send_email(client, auth_headers, call_id, lead_id)

    other_token = create_access_token(uuid.uuid4())
    other_headers = {"Authorization": f"Bearer {other_token}"}

    resp = await client.get(f"/v1/emails/{email_id}", headers=other_headers)
    assert resp.status_code == 404, resp.text
    body = resp.json()
    assert body["error"]["code"] == "EMAIL_NOT_FOUND"
