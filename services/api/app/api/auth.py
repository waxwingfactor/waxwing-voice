"""Auth endpoints — username/password login that mints a Bearer JWT.

Auth: this router is intentionally UNAUTHENTICATED. The login endpoint is the
entry point that issues tokens consumed by every other protected route.

Endpoints:
    POST /v1/auth/login -> { access_token, token_type, expires_in, user }
"""

import logging
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import TOKEN_EXPIRY_HOURS, create_access_token
from app.database import APIError, get_db
from app.limiter import limiter
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserInfoResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


def _verify_password(plaintext: str, password_hash: str | None) -> bool:
    """Return True iff ``plaintext`` matches the bcrypt ``password_hash``.

    Uses a constant-time comparison via passlib. Returns False when the hash
    is missing or malformed so we never accidentally accept an empty password.
    """
    if not password_hash:
        return False
    try:
        from passlib.hash import bcrypt
    except ImportError as exc:  # pragma: no cover — passlib is a hard dependency
        raise APIError(
            status_code=500,
            code="AUTH_BACKEND_UNAVAILABLE",
            message="Password backend is not installed on the server.",
        ) from exc

    try:
        return bcrypt.verify(plaintext, password_hash)
    except (ValueError, TypeError):
        # Malformed hash, wrong scheme, etc. — treat as a verification failure.
        return False


@router.post("/login", response_model=LoginResponse)
@limiter.limit("30/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
) -> LoginResponse:
    """Authenticate a user and return a Bearer JWT.

    DEMO ONLY for the seed admin user
    ---------------------------------
    The seeded admin (``admin@sunsetapartments.example``) is provisioned with
    ``bcrypt.hash("demo")`` by the seed script and Alembic migration 0004.
    Production deployments must rotate this password before exposing the API.

    Real password hashing
    ---------------------
    All non-seed users authenticate against ``users.password_hash`` (bcrypt via
    ``passlib.hash.bcrypt``). The migration adds the column nullable so that
    legacy rows without a hash automatically fail authentication.

    Security
    --------
    - The same 401 INVALID_CREDENTIALS response is returned for "unknown email"
      and "wrong password" so the endpoint cannot be used to enumerate users.
    - The plaintext password is NEVER logged.
    - The minted JWT is NEVER logged.

    Errors:
        401 INVALID_CREDENTIALS — email unknown OR password mismatch.
        422 INVALID_REQUEST     — payload missing fields or malformed email.
    """
    email_lower = body.email.strip().lower() if isinstance(body.email, str) else str(body.email).lower()

    # Log only the domain part of the email to avoid persisting PII in app logs.
    # Full email is still searchable in the DB if an audit is needed.
    _log_email_domain = email_lower.rsplit("@", 1)[-1] if "@" in email_lower else "<no-domain>"
    logger.info("Login attempt for email_domain=%s", _log_email_domain)

    result = await db.execute(
        select(User).where(User.email == email_lower)
    )
    user = result.scalar_one_or_none()

    # Always run a verification step (even when the user is missing) so the
    # response time does not leak whether the email exists. We hash a constant
    # garbage value against a constant garbage hash to spend roughly the same
    # CPU as a real bcrypt check.
    if user is None or not _verify_password(body.password, user.password_hash):
        # Burn a bit of CPU on a dummy verify when no user, mostly to keep
        # timing attacks impractical without depending on a separate function.
        if user is None:
            _verify_password(body.password, "$2b$12$" + "a" * 53)
        raise APIError(
            status_code=401,
            code="INVALID_CREDENTIALS",
            message="Invalid email or password.",
        )

    expires_in = timedelta(hours=TOKEN_EXPIRY_HOURS)
    token = create_access_token(user.company_id, expires_in=expires_in)

    logger.info(
        "Login successful for user_id=%s company_id=%s", user.id, user.company_id
    )

    return LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=int(expires_in.total_seconds()),
        user=UserInfoResponse(
            id=user.id,
            email=user.email,
            name=user.name,
            role=user.role,
            company_id=user.company_id,
        ),
    )


# Re-export for convenience: callers may want to know the demo user UUID.
DEMO_USER_ID: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000002")
