"""Google Calendar adapter — Phase 4.

Wraps the sync google-api-python-client library in asyncio threadpool calls so
it does not block the event loop.

All public functions raise RuntimeError("Google Calendar not configured") when
credentials are missing or the service account file does not exist, allowing
callers to fall back gracefully rather than surfacing a raw provider error.
"""

import asyncio
import os
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

MAX_FREE_SLOTS = 10
OFFICE_HOUR_START = 9  # 09:00 UTC
OFFICE_HOUR_END_SLOT_START = 17  # last slot starts at 17:30, so hour=17, minute=30
SLOT_DURATION_MINUTES = 30

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def _validate_config(calendar_id: str, service_account_path: str) -> None:
    """Raise RuntimeError if the integration is not configured.

    Args:
        calendar_id: Google Calendar ID string.
        service_account_path: Filesystem path to service account JSON.

    Raises:
        RuntimeError: When either value is empty or the file does not exist.
    """
    if not calendar_id or not service_account_path:
        raise RuntimeError("Google Calendar not configured")
    if not os.path.isfile(service_account_path):
        raise RuntimeError("Google Calendar not configured")


def _build_service(service_account_path: str) -> Any:
    """Build a synchronous Google Calendar API service object.

    Args:
        service_account_path: Path to the service account JSON file.

    Returns:
        A googleapiclient Resource object for the calendar v3 API.
    """
    from google.oauth2 import service_account  # type: ignore[import-untyped]
    from googleapiclient.discovery import build  # type: ignore[import-untyped]

    creds = service_account.Credentials.from_service_account_file(
        service_account_path, scopes=SCOPES
    )
    return build("calendar", "v3", credentials=creds)


def _slot_overlaps_busy(
    slot_start: datetime,
    slot_end: datetime,
    busy_periods: list[dict[str, str]],
) -> bool:
    """Return True if [slot_start, slot_end) overlaps any busy period.

    Args:
        slot_start: UTC-aware start of the candidate slot.
        slot_end: UTC-aware end of the candidate slot.
        busy_periods: List of dicts with 'start' and 'end' ISO 8601 strings.

    Returns:
        True when there is overlap, False otherwise.
    """
    for period in busy_periods:
        busy_start = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
        busy_end = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
        # Overlap when slot_start < busy_end AND slot_end > busy_start
        if slot_start < busy_end and slot_end > busy_start:
            return True
    return False


async def get_free_slots(
    calendar_id: str,
    service_account_path: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Return up to 10 free 30-minute slots within office hours for a date range.

    Queries the Google Calendar freebusy API and filters out busy periods,
    then generates candidate 30-minute slots from 09:00–17:30 UTC each day.

    Args:
        calendar_id: Google Calendar ID to query.
        service_account_path: Path to service account JSON file.
        start_date: First day of the range (inclusive).
        end_date: Last day of the range (inclusive).

    Returns:
        List of dicts with keys: date, start_time, end_time, slot_id.
        At most MAX_FREE_SLOTS (10) entries are returned.

    Raises:
        RuntimeError: "Google Calendar not configured" when credentials are
            missing or the service account file does not exist.
        Exception: Any Google API error propagates to the caller, which must
            handle it and fall back gracefully.
    """
    _validate_config(calendar_id, service_account_path)

    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, lambda: _build_service(service_account_path))

    start_dt = datetime.combine(start_date, time(0, 0), tzinfo=UTC).isoformat()
    end_dt = datetime.combine(end_date + timedelta(days=1), time(0, 0), tzinfo=UTC).isoformat()

    freebusy_body = {
        "timeMin": start_dt,
        "timeMax": end_dt,
        "items": [{"id": calendar_id}],
    }

    freebusy_result = await loop.run_in_executor(
        None,
        lambda: service.freebusy().query(body=freebusy_body).execute(),
    )
    busy_periods: list[dict[str, str]] = (
        freebusy_result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
    )

    free_slots: list[dict[str, Any]] = []
    current_day = start_date

    while current_day <= end_date and len(free_slots) < MAX_FREE_SLOTS:
        # Candidate slot starts: 09:00, 09:30, ..., 17:30 (last 30-min slot ending at 18:00)
        slot_hour = OFFICE_HOUR_START
        slot_minute = 0

        while (
            slot_hour < OFFICE_HOUR_END_SLOT_START
            or (slot_hour == OFFICE_HOUR_END_SLOT_START and slot_minute <= 30)
        ) and len(free_slots) < MAX_FREE_SLOTS:  # noqa: E501
            slot_start_dt = datetime(
                current_day.year,
                current_day.month,
                current_day.day,
                slot_hour,
                slot_minute,
                tzinfo=UTC,
            )
            slot_end_dt = slot_start_dt + timedelta(minutes=SLOT_DURATION_MINUTES)

            if not _slot_overlaps_busy(slot_start_dt, slot_end_dt, busy_periods):
                slot_id = f"gcal-{calendar_id[:8]}-{current_day}-{slot_start_dt.strftime('%H%M')}"
                free_slots.append(
                    {
                        "date": current_day,
                        "start_time": slot_start_dt.time(),
                        "end_time": slot_end_dt.time(),
                        "slot_id": slot_id,
                    }
                )

            # Advance by 30 minutes
            slot_minute += SLOT_DURATION_MINUTES
            if slot_minute >= 60:
                slot_hour += 1
                slot_minute -= 60

        current_day += timedelta(days=1)

    return free_slots


async def create_calendar_event(
    calendar_id: str,
    service_account_path: str,
    summary: str,
    start_dt: datetime,
    end_dt: datetime,
    attendee_email: str | None = None,
) -> str:
    """Create a Google Calendar event and return its event ID.

    Args:
        calendar_id: Target Google Calendar ID.
        service_account_path: Path to service account JSON file.
        summary: Event title/summary string.
        start_dt: UTC-aware start datetime.
        end_dt: UTC-aware end datetime.
        attendee_email: Optional attendee email — triggers a calendar invite.

    Returns:
        The Google Calendar event ID string.

    Raises:
        RuntimeError: "Google Calendar not configured" when credentials are
            missing or the service account file does not exist.
        Exception: Any Google API error propagates to the caller.
    """
    _validate_config(calendar_id, service_account_path)

    loop = asyncio.get_event_loop()
    service = await loop.run_in_executor(None, lambda: _build_service(service_account_path))

    event_body: dict[str, Any] = {
        "summary": summary,
        "start": {"dateTime": start_dt.isoformat(), "timeZone": "UTC"},
        "end": {"dateTime": end_dt.isoformat(), "timeZone": "UTC"},
    }
    if attendee_email:
        event_body["attendees"] = [{"email": attendee_email}]

    event = await loop.run_in_executor(
        None,
        lambda: (
            service.events()
            .insert(calendarId=calendar_id, body=event_body, sendUpdates="all")
            .execute()
        ),
    )
    return event["id"]
