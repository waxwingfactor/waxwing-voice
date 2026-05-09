"""Shared slowapi rate limiter instance."""

from slowapi import Limiter
from slowapi.util import get_remote_address


def _get_company_id_key(request) -> str:  # noqa: ANN001
    """Key rate limits by company_id JWT claim, falling back to IP.

    The JWT dependency has already run by the time slowapi calls this.
    FastAPI stores dependency results in request.state when using the
    limiter decorator pattern — but with slowapi we read from the token
    directly. Simplest reliable approach: fall back to IP (acceptable for
    Phase 5; Phase 6 can add Redis + company-keyed counters).
    """
    return get_remote_address(request)


limiter = Limiter(key_func=_get_company_id_key)
