"""Integrations status endpoint tests.

Covers GET /v1/integrations/calendar/status — both the "nothing configured"
fallback and the "fully configured" happy path.

We patch ``app.api.integrations.get_settings`` directly with monkeypatch so the
test runs deterministically regardless of what is in the local .env. This avoids
fighting with the @lru_cache decorator on the real ``get_settings``.
"""

import os
from datetime import datetime

import pytest
from httpx import AsyncClient

import app.api.integrations as integrations_module
from app.config import Settings


def _make_settings(**overrides) -> Settings:
    """Build a Settings instance with sensible defaults for testing."""
    base = {
        "database_url": "postgresql+asyncpg://placeholder/test",
        "google_service_account_path": "",
        "google_calendar_id": "",
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_calendar_status_not_configured(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no GCal env vars set, connected=False and errors are populated."""
    monkeypatch.setattr(
        integrations_module,
        "get_settings",
        lambda: _make_settings(
            google_service_account_path="",
            google_calendar_id="",
        ),
    )

    resp = await client.get(
        "/v1/integrations/calendar/status",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connected"] is False
    assert data["service_account_configured"] is False
    assert data["calendar_id"] is None
    assert isinstance(data["errors"], list)
    assert len(data["errors"]) >= 2  # one for path, one for calendar id
    assert "last_check_at" in data
    # last_check_at must parse as ISO-8601
    datetime.fromisoformat(data["last_check_at"].replace("Z", "+00:00"))


@pytest.mark.asyncio
async def test_calendar_status_fully_configured(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """With both env vars set and the file existing, connected=True with no errors."""
    fake_sa_file = tmp_path / "fake-service-account.json"
    fake_sa_file.write_text('{"type": "service_account"}', encoding="utf-8")

    monkeypatch.setattr(
        integrations_module,
        "get_settings",
        lambda: _make_settings(
            google_service_account_path=str(fake_sa_file),
            google_calendar_id="leasing-team@group.calendar.google.com",
        ),
    )

    resp = await client.get(
        "/v1/integrations/calendar/status",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connected"] is True
    assert data["service_account_configured"] is True
    # Calendar ID is masked in the response.
    assert data["calendar_id"] == "xxxx@group.calendar.google.com"
    assert data["errors"] == []


@pytest.mark.asyncio
async def test_calendar_status_path_set_but_file_missing(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """If the path is set but the file doesn't exist, connected=False with a clear error."""
    missing_path = str(tmp_path / "does-not-exist.json")
    assert not os.path.isfile(missing_path)

    monkeypatch.setattr(
        integrations_module,
        "get_settings",
        lambda: _make_settings(
            google_service_account_path=missing_path,
            google_calendar_id="primary",
        ),
    )

    resp = await client.get(
        "/v1/integrations/calendar/status",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["connected"] is False
    assert data["service_account_configured"] is False
    # Calendar ID set (no @) -> masked to "xxxx"
    assert data["calendar_id"] == "xxxx"
    assert any("not found" in err.lower() for err in data["errors"])
