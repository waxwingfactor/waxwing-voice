"""Database engine, session factory, and shared FastAPI dependencies.

Consumers:
- All routers import `get_db` and `get_company_id` via Depends().
- `APIError` is raised by all routers; the exception handler in main.py
  converts it to the canonical JSON error envelope.
"""

import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import Header
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings


def _normalize_database_url(url: str) -> str:
    """Ensure the URL uses the postgresql+asyncpg:// scheme.

    Alembic env.py accepts either scheme; we always coerce to asyncpg here.
    """
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


_settings = get_settings()
_db_url = _normalize_database_url(_settings.database_url)

engine = create_async_engine(
    _db_url,
    echo=_settings.environment == "local",
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# Structured error — raised by routers, caught by the exception handler
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Raise this instead of HTTPException to produce the canonical error envelope.

    Example:
        raise APIError(404, "PROPERTY_NOT_FOUND", "Property not found or not in company scope")
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        suggested_action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        # Included in voice-facing errors to help callers understand next steps.
        # Not surfaced in the wire format (main.py omits it) — voice router can
        # inject it into the payload manually when needed.
        self.suggested_action = suggested_action

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async DB session.

    Commits on clean exit; rolls back on any exception so that partial writes
    never leak into a subsequent request on the same connection.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_company_id(x_company_id: str = Header(...)) -> uuid.UUID:
    """Parse the X-Company-Id request header into a UUID.

    Phase 1: header is trusted as-is (no JWT verification).
    Phase 2: replace with JWT claims extraction.

    Raises:
        APIError: 401 UNAUTHORIZED if the header is missing or not a valid UUID.
    """
    try:
        return uuid.UUID(x_company_id)
    except (ValueError, AttributeError) as exc:
        raise APIError(
            status_code=401,
            code="UNAUTHORIZED",
            message="X-Company-Id header must be a valid UUID.",
        ) from exc
