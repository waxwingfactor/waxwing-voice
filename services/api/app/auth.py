"""JWT token utilities — used by tests and seeding scripts to generate valid tokens."""

import uuid
from datetime import UTC, datetime, timedelta

import jwt

from app.config import get_settings

TOKEN_EXPIRY_HOURS = 24


def create_access_token(company_id: uuid.UUID, expires_in: timedelta | None = None) -> str:
    """Create a signed JWT for the given company.

    Args:
        company_id: UUID of the company to embed in the token.
        expires_in: Token lifetime; defaults to TOKEN_EXPIRY_HOURS.

    Returns:
        Encoded JWT string.
    """
    settings = get_settings()
    expire = datetime.now(UTC) + (expires_in or timedelta(hours=TOKEN_EXPIRY_HOURS))
    payload = {
        "company_id": str(company_id),
        "exp": expire,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
