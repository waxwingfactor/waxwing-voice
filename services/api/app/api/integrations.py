"""Integrations status endpoints — surface configuration health to the dashboard.

Auth: Bearer JWT (see app.database.get_company_id).

Endpoints:
    GET /v1/integrations/calendar/status -> CalendarStatusResponse

These endpoints report **configuration sanity**, not live third-party API
health. They do not make outbound calls (which would require valid auth flows
and could be slow/unreliable). The intent is to give the dashboard a quick
"is this integration set up?" signal.
"""

import logging
import os
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from app.config import get_settings
from app.database import get_company_id
from app.limiter import limiter
from app.schemas.integrations import CalendarStatusResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/integrations", tags=["integrations"])


def _mask_calendar_id(calendar_id: str) -> str:
    """Return a redacted version of a calendar ID safe for client display.

    Examples:
        "abc123@group.calendar.google.com" -> "xxxx@group.calendar.google.com"
        "primary"                          -> "xxxx"
        ""                                  -> ""
    """
    if not calendar_id:
        return ""
    if "@" in calendar_id:
        _local, domain = calendar_id.split("@", 1)
        return f"xxxx@{domain}"
    return "xxxx"


@router.get("/calendar/status", response_model=CalendarStatusResponse)
@limiter.limit("60/minute")
async def calendar_status(
    request: Request,
    company_id: uuid.UUID = Depends(get_company_id),
) -> CalendarStatusResponse:
    """Return a config-sanity snapshot of the Google Calendar integration.

    What this checks
    ----------------
    1. ``GOOGLE_SERVICE_ACCOUNT_PATH`` env var is non-empty.
    2. The file at that path exists on disk.
    3. ``GOOGLE_CALENDAR_ID`` env var is non-empty.

    What this does NOT do
    ---------------------
    - It does not load the service account JSON or verify its private key.
    - It does not call the Google Calendar API.
    - It does not check that the service account has access to the calendar.

    These deeper checks belong in a separate readiness probe that operators
    can opt into; the dashboard does not need them to render the "Integrations"
    page.

    Errors:
        Never raises — diagnostic information is returned in the ``errors``
        field of the response body alongside ``connected: false``.
    """
    settings = get_settings()
    errors: list[str] = []

    sa_path = settings.google_service_account_path or ""
    cal_id = settings.google_calendar_id or ""

    sa_path_set = bool(sa_path)
    sa_file_exists = bool(sa_path) and os.path.isfile(sa_path)
    cal_id_set = bool(cal_id)

    if not sa_path_set:
        errors.append("GOOGLE_SERVICE_ACCOUNT_PATH is not set in the environment.")
    elif not sa_file_exists:
        errors.append(f"Service account file not found at path: {sa_path!r}")

    if not cal_id_set:
        errors.append("GOOGLE_CALENDAR_ID is not set in the environment.")

    service_account_configured = sa_path_set and sa_file_exists
    connected = service_account_configured and cal_id_set

    logger.info(
        "Calendar status check: company_id=%s connected=%s service_account_configured=%s",
        company_id,
        connected,
        service_account_configured,
    )

    return CalendarStatusResponse(
        connected=connected,
        calendar_id=_mask_calendar_id(cal_id) if cal_id_set else None,
        service_account_configured=service_account_configured,
        last_check_at=datetime.now(UTC),
        errors=errors,
    )
