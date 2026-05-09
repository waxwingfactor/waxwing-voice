"""Shared fixtures for integration tests.

All tests run against the real FastAPI app using httpx ASGITransport.
The test DB is whatever DATABASE_URL is in services/api/.env.

Seed data MUST be loaded before running these tests:
    cd services/api
    uv run python ../../tests/fixtures/seed_property.py

Event loop strategy:
    asyncio_default_fixture_loop_scope = "session" (set in pyproject.toml) means
    all async fixtures share the session event loop. The SQLAlchemy engine is a
    module-level singleton bound to one event loop, so all fixtures and tests
    must run on that same loop.

    The httpx AsyncClient is session-scoped to match the engine's lifetime.
    call_id and lead_id remain function-scoped (fresh records per test) but
    explicitly run on the session loop via loop_scope="session".

    engine_teardown disposes the SQLAlchemy connection pool before the session
    loop closes, preventing ResourceWarning from unclosed asyncpg transports
    on Windows (ProactorEventLoop).
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.auth import create_access_token
from app.database import engine
from app.main import app

# ---------------------------------------------------------------------------
# Engine teardown — must run before the session event loop closes
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(autouse=True, loop_scope="session", scope="session")
async def engine_teardown() -> AsyncGenerator[None, None]:
    """Dispose the SQLAlchemy async engine after all tests complete.

    asyncpg connections hold open TCP transports. If the engine is not
    disposed before the event loop shuts down, Python's garbage collector
    fires __del__ callbacks after the loop is gone, emitting ResourceWarning
    (treated as errors by filterwarnings = ["error"]).

    This fixture ensures connections are closed cleanly while the session loop
    is still alive.
    """
    yield
    await engine.dispose()


# ---------------------------------------------------------------------------
# Constants — mirror the deterministic seed UUIDs
# ---------------------------------------------------------------------------

# Mirror the deterministic seed UUIDs from tests/fixtures/seed_property.py
COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
PROPERTY_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")

# A stable test caller phone — used as the upsert key for lead idempotency tests.
CALLER_PHONE = "+15125559001"


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Valid Bearer token scoped to COMPANY_ID."""
    token = create_access_token(COMPANY_ID)
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async test client wired directly to the live ASGI app (no network I/O).

    Session-scoped to share the same event loop as the SQLAlchemy engine
    singleton (module-level in app.database).
    """
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


@pytest_asyncio.fixture(loop_scope="session")
async def call_id(client: AsyncClient, auth_headers: dict[str, str]) -> uuid.UUID:
    """Create a fresh call record and return its UUID.

    Each fixture invocation generates a unique twilio_call_sid so that
    re-running the test suite does not hit the unique constraint.

    Explicitly runs on the session loop so the DB write lands on the same
    connection pool as the engine.
    """
    resp = await client.post(
        "/v1/calls/",
        json={
            "property_id": str(PROPERTY_ID),
            "twilio_call_sid": str(uuid.uuid4()),
            "caller_phone": CALLER_PHONE,
            "started_at": datetime.now(UTC).isoformat(),
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return uuid.UUID(resp.json()["id"])


@pytest_asyncio.fixture(loop_scope="session")
async def lead_id(
    client: AsyncClient,
    auth_headers: dict[str, str],
    call_id: uuid.UUID,
) -> uuid.UUID:
    """Upsert a lead and return its UUID.

    Uses a fixed phone number so repeat runs merge into the same lead row
    (idempotent by design — the upsert constraint is (property_id, phone)).

    Explicitly runs on the session loop.
    """
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
    return uuid.UUID(resp.json()["lead_id"])
