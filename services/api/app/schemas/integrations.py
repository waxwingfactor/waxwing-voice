"""Pydantic schemas for the integrations status endpoints."""

from datetime import datetime

from pydantic import BaseModel, Field


class CalendarStatusResponse(BaseModel):
    """Result of GET /v1/integrations/calendar/status — config sanity, not live API health."""

    connected: bool = Field(
        ...,
        description=(
            "True iff GOOGLE_SERVICE_ACCOUNT_PATH is set, the file exists on "
            "disk, and GOOGLE_CALENDAR_ID is set. Does NOT call the Google API."
        ),
    )
    calendar_id: str | None = Field(
        default=None,
        description=(
            "Masked calendar ID — local part is replaced with 'xxxx' to avoid "
            "leaking the address publicly. None when not configured."
        ),
    )
    service_account_configured: bool = Field(
        ...,
        description="True iff GOOGLE_SERVICE_ACCOUNT_PATH is set AND the file exists.",
    )
    last_check_at: datetime = Field(
        ...,
        description="Server-side wall-clock UTC timestamp at which this status was computed.",
    )
    errors: list[str] = Field(
        default_factory=list,
        description="Diagnostic messages explaining why connected=false.",
    )
