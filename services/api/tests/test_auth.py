"""JWT auth enforcement tests + login endpoint tests.

The first half uses POST /v1/voice/events as the representative protected
endpoint to confirm that the Bearer-token middleware rejects requests correctly.

The second half covers POST /v1/auth/login (the only intentionally unauth'd
endpoint) — happy path, wrong password, unknown email, and missing fields.
"""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient

from app.auth import create_access_token

DEMO_EMAIL = "admin@sunsetapartments.example"
DEMO_PASSWORD = "demo"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EVENT_PAYLOAD = {
    "call_id": str(uuid.uuid4()),
    "event_type": "call_started",
    "payload": {},
    "occurred_at": "2026-05-09T12:00:00Z",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_auth_header(client: AsyncClient) -> None:
    """No Authorization header at all should return 403.

    FastAPI's HTTPBearer security scheme returns 403 when the Authorization
    header is completely absent (not just malformed). This is consistent with
    RFC 6750 which distinguishes "no credentials" from "invalid credentials".

    Note: FastAPI >= 0.95 with HTTPBearer returns 403 for a missing header
    and 403 for a non-Bearer scheme (e.g. Basic). Only an invalid Bearer
    token format returns 401 from our get_company_id dependency.
    """
    resp = await client.post("/v1/voice/events", json=_EVENT_PAYLOAD)
    # FastAPI's HTTPBearer returns 403 when the Authorization header is absent.
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_invalid_token(client: AsyncClient) -> None:
    """A structurally invalid JWT string should return 401 UNAUTHORIZED."""
    resp = await client.post(
        "/v1/voice/events",
        json=_EVENT_PAYLOAD,
        headers={"Authorization": "Bearer this.is.garbage"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert body["error"]["retryable"] is False


@pytest.mark.asyncio
async def test_expired_token(client: AsyncClient) -> None:
    """An already-expired token should return 401 UNAUTHORIZED."""
    token = create_access_token(
        uuid.UUID("00000000-0000-0000-0000-000000000001"),
        expires_in=timedelta(seconds=-1),
    )
    resp = await client.post(
        "/v1/voice/events",
        json=_EVENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_valid_token_wrong_company_gets_404_not_401(client: AsyncClient) -> None:
    """A valid token for a different company should return 404 (not 401).

    Auth succeeds; the resource is just not found for that company scope.
    This confirms the middleware does not conflate authorization with
    multi-tenant scoping.
    """
    other_company_id = uuid.uuid4()
    token = create_access_token(other_company_id)
    resp = await client.post(
        "/v1/voice/events",
        json=_EVENT_PAYLOAD,
        headers={"Authorization": f"Bearer {token}"},
    )
    # The token is valid — auth passes. But the call_id is unknown for this
    # company so the route returns 404 CALL_NOT_FOUND, not 401.
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "CALL_NOT_FOUND"


# ---------------------------------------------------------------------------
# POST /v1/auth/login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success_returns_token_and_user(client: AsyncClient) -> None:
    """Login with the seed admin returns 200 + a usable Bearer JWT."""
    resp = await client.post(
        "/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert isinstance(data["access_token"], str) and len(data["access_token"]) > 20
    assert data["expires_in"] > 0
    assert data["user"]["email"] == DEMO_EMAIL
    assert data["user"]["role"] == "admin"
    assert "company_id" in data["user"]

    # The minted token should successfully authenticate against a real endpoint.
    auth = {"Authorization": f"Bearer {data['access_token']}"}
    me = await client.get(f"/v1/properties/{uuid.uuid4()}", headers=auth)
    # 404 (not 401/403) confirms auth succeeded; resource just doesn't exist.
    assert me.status_code == 404, me.text


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    """Login with a known email but wrong password returns 401 INVALID_CREDENTIALS."""
    resp = await client.post(
        "/v1/auth/login",
        json={"email": DEMO_EMAIL, "password": "definitely-not-the-right-password"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_unknown_email_returns_401(client: AsyncClient) -> None:
    """Login with an unknown email also returns 401 INVALID_CREDENTIALS (no enum leak)."""
    resp = await client.post(
        "/v1/auth/login",
        json={"email": "no-such-user@example.com", "password": "whatever"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_missing_fields_returns_422(client: AsyncClient) -> None:
    """Login with no body returns 422 from Pydantic validation."""
    resp = await client.post("/v1/auth/login", json={})
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_login_minted_token_expires(client: AsyncClient) -> None:
    """An already-expired token from create_access_token is rejected as 401.

    Re-uses the existing test_expired_token coverage via a direct mint to keep
    the auth-login test surface complete in one place.
    """
    token = create_access_token(
        uuid.UUID("00000000-0000-0000-0000-000000000001"),
        expires_in=timedelta(seconds=-1),
    )
    resp = await client.get(
        "/v1/properties/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"
