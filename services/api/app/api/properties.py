"""Property endpoints — consumed by Alex (dashboard) and Akhil (get_property_profile tool).

Auth: X-Company-Id header (Phase 1 placeholder — UUID trusted as-is).

Endpoints:
    GET  /v1/properties/                      -> paginated property list
    GET  /v1/properties/{property_id}         -> full property detail
    GET  /v1/properties/{property_id}/summary -> aggregated daily metrics
"""

import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.models.booking import Booking
from app.models.call import Call
from app.models.email_record import EmailRecord
from app.models.lead import Lead
from app.models.property import Property
from app.schemas.pagination import PaginatedResponse
from app.schemas.properties import PropertyDetailResponse, PropertyListItem, PropertySummaryResponse

router = APIRouter(prefix="/properties", tags=["properties"])


# ---------------------------------------------------------------------------
# GET /properties/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[PropertyListItem])
async def list_properties(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[PropertyListItem]:
    """Return a paginated list of properties scoped to the caller's company.

    Consumer Notes (Alex — dashboard):
        - Use `page` and `page_size` for the property selector sidebar.
        - `total` is the record count across all pages.

    Example:
        GET /v1/properties/?page=1&page_size=20
        X-Company-Id: <company-uuid>
    """
    base_where = Property.company_id == company_id

    total: int = await db.scalar(select(func.count()).select_from(Property).where(base_where)) or 0

    result = await db.execute(
        select(Property)
        .where(base_where)
        .order_by(Property.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    properties = result.scalars().all()

    return PaginatedResponse[PropertyListItem](
        items=[PropertyListItem.model_validate(p) for p in properties],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /properties/{property_id}
# ---------------------------------------------------------------------------


@router.get("/{property_id}", response_model=PropertyDetailResponse)
async def get_property(
    property_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PropertyDetailResponse:
    """Return the full property profile.

    Consumer Notes:
        - Alex (dashboard): property settings page and detail view.
        - Akhil (voice tool get_property_profile): amenities, office_hours,
          leasing_policies, escalation_contacts, call_handling_rules.

    Errors:
        404 PROPERTY_NOT_FOUND — property does not exist in this company.

    Example:
        GET /v1/properties/a1b2c3d4-0000-0000-0000-000000000001
        X-Company-Id: <company-uuid>
    """
    prop = await _get_property_or_404(db, property_id, company_id)
    return PropertyDetailResponse.model_validate(prop)


# ---------------------------------------------------------------------------
# GET /properties/{property_id}/summary
# ---------------------------------------------------------------------------


@router.get("/{property_id}/summary", response_model=PropertySummaryResponse)
async def get_property_summary(
    property_id: uuid.UUID,
    date: str = Query(..., description="Reporting day in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PropertySummaryResponse:
    """Return aggregated daily metrics for the dashboard home tiles.

    Consumer Notes (Alex — dashboard):
        - Drives the 6 metric tiles on the property home page.
        - Call once per page load; refresh on user request.
        - `open_action_items` counts non-abandoned calls with action_items set.

    Errors:
        400 INVALID_REQUEST    — `date` is not YYYY-MM-DD.
        404 PROPERTY_NOT_FOUND — property not in company scope.

    Example:
        GET /v1/properties/{id}/summary?date=2026-05-09
        X-Company-Id: <company-uuid>
    """
    try:
        date_obj = date_type.fromisoformat(date)
    except ValueError as exc:
        raise APIError(
            status_code=400,
            code="INVALID_REQUEST",
            message=f"date must be YYYY-MM-DD, got: {date!r}",
        ) from exc

    prop = await _get_property_or_404(db, property_id, company_id)
    pid = prop.id

    calls_today: int = (
        await db.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.property_id == pid,
                Call.company_id == company_id,
                cast(Call.created_at, Date) == date_obj,
            )
        )
        or 0
    )

    escalations_today: int = (
        await db.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.property_id == pid,
                Call.company_id == company_id,
                cast(Call.created_at, Date) == date_obj,
                Call.escalation_flag.is_(True),
            )
        )
        or 0
    )

    new_leads_today: int = (
        await db.scalar(
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.property_id == pid,
                Lead.company_id == company_id,
                cast(Lead.created_at, Date) == date_obj,
            )
        )
        or 0
    )

    tours_booked_today: int = (
        await db.scalar(
            select(func.count())
            .select_from(Booking)
            .where(
                Booking.property_id == pid,
                Booking.company_id == company_id,
                cast(Booking.created_at, Date) == date_obj,
                Booking.status == "confirmed",
            )
        )
        or 0
    )

    follow_ups_sent_today: int = (
        await db.scalar(
            select(func.count())
            .select_from(EmailRecord)
            .where(
                EmailRecord.property_id == pid,
                EmailRecord.company_id == company_id,
                cast(EmailRecord.created_at, Date) == date_obj,
                EmailRecord.delivery_status == "sent",
            )
        )
        or 0
    )

    open_action_items: int = (
        await db.scalar(
            select(func.count())
            .select_from(Call)
            .where(
                Call.property_id == pid,
                Call.company_id == company_id,
                Call.action_items.isnot(None),
                Call.status != "abandoned",
            )
        )
        or 0
    )

    return PropertySummaryResponse(
        property_id=prop.id,
        property_name=prop.name,
        date=date,
        calls_today=calls_today,
        new_leads_today=new_leads_today,
        tours_booked_today=tours_booked_today,
        escalations_today=escalations_today,
        follow_ups_sent_today=follow_ups_sent_today,
        open_action_items=open_action_items,
    )


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


async def _get_property_or_404(
    db: AsyncSession,
    property_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Property:
    """Fetch a property scoped to company_id; raise 404 PROPERTY_NOT_FOUND if absent.

    Args:
        db: Active async session.
        property_id: UUID of the property to fetch.
        company_id: Company scope extracted from X-Company-Id header.

    Returns:
        The ORM Property instance.

    Raises:
        APIError: 404 PROPERTY_NOT_FOUND.
    """
    result = await db.execute(
        select(Property).where(
            Property.id == property_id,
            Property.company_id == company_id,
        )
    )
    prop = result.scalar_one_or_none()
    if prop is None:
        raise APIError(
            status_code=404,
            code="PROPERTY_NOT_FOUND",
            message="Property not found or does not belong to this company.",
        )
    return prop
