"""Booking endpoints — consumed by Alex (dashboard tour calendar + lead/call detail views).

Auth: Bearer JWT (see app.database.get_company_id).

Endpoints:
    GET /v1/bookings/                -> paginated booking list (filtered by property)
    GET /v1/bookings/{booking_id}    -> full booking detail
"""

import logging
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.booking import Booking
from app.models.property import Property
from app.schemas.bookings import BookingDetailResponse, BookingListItem
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings", tags=["bookings"])


# ---------------------------------------------------------------------------
# GET /bookings/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[BookingListItem])
@limiter.limit("60/minute")
async def list_bookings(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    status: str | None = Query(
        default=None,
        description="confirmed | cancelled | rescheduled | no_show | completed",
    ),
    lead_id: uuid.UUID | None = Query(default=None, description="Filter by lead"),
    date_from: str | None = Query(
        default=None,
        description="YYYY-MM-DD — inclusive start, filters on tour_date",
    ),
    date_to: str | None = Query(
        default=None,
        description="YYYY-MM-DD — inclusive end, filters on tour_date",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[BookingListItem]:
    """Return a paginated booking list filtered by property.

    Consumer Notes (Alex — dashboard):
        - Always pass `property_id` — cross-property booking lists are not supported.
        - `date_from` / `date_to` filter on `tour_date` (the day of the tour),
          not on `created_at`. This matches how a calendar view expects to query.
        - `status` values are: confirmed, cancelled, rescheduled, no_show, completed.
        - Results are ordered by `tour_date DESC, start_time DESC` so the most
          recent / upcoming tours surface first.

    Errors:
        400 INVALID_REQUEST    — date_from / date_to is not YYYY-MM-DD.
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/bookings/?property_id=...&status=confirmed&date_from=2026-05-01
    """
    # Verify property belongs to this company (404 — never 403 — on scope mismatch)
    prop_result = await db.execute(
        select(Property).where(
            Property.id == property_id,
            Property.company_id == company_id,
        )
    )
    if prop_result.scalar_one_or_none() is None:
        raise APIError(
            status_code=404,
            code="PROPERTY_NOT_FOUND",
            message="Property not found or does not belong to this company.",
        )

    filters = [
        Booking.property_id == property_id,
        Booking.company_id == company_id,
    ]
    if status is not None:
        filters.append(Booking.status == status)
    if lead_id is not None:
        filters.append(Booking.lead_id == lead_id)
    if date_from is not None:
        try:
            filters.append(Booking.tour_date >= date_type.fromisoformat(date_from))
        except ValueError as exc:
            raise APIError(
                400,
                "INVALID_REQUEST",
                f"date_from must be YYYY-MM-DD, got: {date_from!r}",
            ) from exc
    if date_to is not None:
        try:
            filters.append(Booking.tour_date <= date_type.fromisoformat(date_to))
        except ValueError as exc:
            raise APIError(
                400,
                "INVALID_REQUEST",
                f"date_to must be YYYY-MM-DD, got: {date_to!r}",
            ) from exc

    total: int = await db.scalar(select(func.count()).select_from(Booking).where(*filters)) or 0

    result = await db.execute(
        select(Booking)
        .where(*filters)
        .order_by(Booking.tour_date.desc(), Booking.start_time.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    bookings = result.scalars().all()

    if total > 0:
        logger.info(
            "list_bookings property_id=%s company_id=%s total=%d page=%d status=%s",
            property_id,
            company_id,
            total,
            page,
            status,
        )

    return PaginatedResponse[BookingListItem](
        items=[BookingListItem.model_validate(b) for b in bookings],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /bookings/{booking_id}
# ---------------------------------------------------------------------------


@router.get("/{booking_id}", response_model=BookingDetailResponse)
@limiter.limit("60/minute")
async def get_booking(
    request: Request,
    booking_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> BookingDetailResponse:
    """Return the full booking record.

    Consumer Notes (Alex — dashboard):
        - All booking fields including calendar provider metadata are returned.
        - Scope check is by `company_id` (the booking already carries it),
          which transitively covers the property→company relationship.

    Errors:
        404 BOOKING_NOT_FOUND — booking does not exist in company scope.

    Example:
        GET /v1/bookings/a1b2c3d4-0000-0000-0000-000000000050
    """
    result = await db.execute(
        select(Booking).where(
            Booking.id == booking_id,
            Booking.company_id == company_id,
        )
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise APIError(
            status_code=404,
            code="BOOKING_NOT_FOUND",
            message="Booking not found or does not belong to this company.",
        )

    return BookingDetailResponse.model_validate(booking)
