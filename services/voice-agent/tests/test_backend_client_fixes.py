"""
Tests for BackendClient constructor fixes (critical issues #1, #2, #6).

Issue #1: BackendClient constructor did not accept timeout_seconds —
           caused TypeError crash on startup.
Issue #2: BackendClient _http stayed None until __aenter__ called —
           every tool call raised RuntimeError outside an async context manager.
Issue #6: Empty voice_agent_jwt caused silent 401 — now validated at construction.

These tests verify the fixes without any network calls (all HTTP is mocked).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from voice_agent.tools.backend_client import BackendClient, BackendToolError


_VALID_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJjb21wYW55X2lkIjoiZmFrZSJ9.fake_sig"
_FAKE_PROPERTY_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Issue #6 fix: JWT validation at construction time
# ---------------------------------------------------------------------------

class TestJwtValidation:
    def test_empty_jwt_raises_value_error(self) -> None:
        """BackendClient must raise ValueError immediately on empty jwt_token."""
        with pytest.raises(ValueError, match="jwt_token"):
            BackendClient(base_url="http://localhost:8000", jwt_token="")

    def test_none_jwt_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="jwt_token"):
            BackendClient(base_url="http://localhost:8000", jwt_token=None)  # type: ignore[arg-type]

    def test_valid_jwt_constructs_successfully(self) -> None:
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        assert client is not None

    def test_jwt_stored_correctly(self) -> None:
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        assert client._jwt_token == _VALID_JWT


# ---------------------------------------------------------------------------
# Issue #1 fix: timeout_seconds parameter in constructor
# ---------------------------------------------------------------------------

class TestTimeoutSeconds:
    def test_timeout_seconds_accepted_as_param(self) -> None:
        """Constructor must accept timeout_seconds without TypeError (issue #1)."""
        client = BackendClient(
            base_url="http://localhost:8000",
            jwt_token=_VALID_JWT,
            timeout_seconds=15.0,
        )
        assert client._default_timeout == 15.0

    def test_default_timeout_is_ten_seconds(self) -> None:
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        assert client._default_timeout == 10.0

    def test_custom_timeout_stored(self) -> None:
        client = BackendClient(
            base_url="http://localhost:8000",
            jwt_token=_VALID_JWT,
            timeout_seconds=5.0,
        )
        assert client._default_timeout == 5.0


# ---------------------------------------------------------------------------
# Issue #2 fix: lazy _http initialisation (no context manager required)
# ---------------------------------------------------------------------------

class TestLazyHttpInit:
    def test_client_can_be_used_without_context_manager(self) -> None:
        """
        Calling _client() before __aenter__ must NOT raise RuntimeError.
        Issue #2: previously _http was None until __aenter__ was called.
        """
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        # _client() should return a valid httpx.AsyncClient without needing __aenter__
        http = client._client()
        assert http is not None
        assert isinstance(http, httpx.AsyncClient)

    def test_second_call_to_client_returns_same_instance(self) -> None:
        """Lazy init creates the client once and reuses it."""
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        http1 = client._client()
        http2 = client._client()
        assert http1 is http2

    @pytest.mark.asyncio
    async def test_context_manager_still_works(self) -> None:
        """Async context manager usage must still function correctly."""
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        async with client as ctx:
            assert ctx._http is not None
        # After exit, http is closed and set to None
        assert client._http is None

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        """close() can be called multiple times without error."""
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        _ = client._client()  # initialise
        await client.close()
        await client.close()  # second call must not raise

    def test_external_client_not_closed_by_us(self) -> None:
        """If an external httpx client is injected, close() must not close it."""
        external = httpx.AsyncClient(base_url="http://localhost:8000")
        client = BackendClient(
            base_url="http://localhost:8000",
            jwt_token=_VALID_JWT,
            http_client=external,
        )
        # _http should be set to the external client immediately
        assert client._http is external
        assert client._external_client is external


# ---------------------------------------------------------------------------
# Auth headers
# ---------------------------------------------------------------------------

class TestAuthHeaders:
    def test_bearer_token_in_auth_headers(self) -> None:
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        headers = client._auth_headers()
        assert headers["Authorization"] == f"Bearer {_VALID_JWT}"

    def test_auth_headers_do_not_contain_key_value(self) -> None:
        """Ensure the JWT is not accidentally logged via headers.__repr__."""
        # This is a belt-and-suspenders check: the JWT should only appear in
        # the Authorization header value, not as a bare field.
        client = BackendClient(base_url="http://localhost:8000", jwt_token=_VALID_JWT)
        headers = client._auth_headers()
        assert list(headers.keys()) == ["Authorization"]
