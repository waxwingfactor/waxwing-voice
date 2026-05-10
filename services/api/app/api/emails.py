"""Email endpoints — consumed by Alex (dashboard email log + lead/call detail views).

Auth: Bearer JWT (see app.database.get_company_id).

Endpoints:
    GET /v1/emails/              -> paginated email list (filtered by property)
    GET /v1/emails/{email_id}    -> full email detail (subject + body)
"""

import logging
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.email_record import EmailRecord
from app.models.property import Property
from app.schemas.emails import EmailDetailResponse, EmailListItem
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/emails", tags=["emails"])


# ---------------------------------------------------------------------------
# GET /emails/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[EmailListItem])
@limiter.limit("60/minute")
async def list_emails(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    lead_id: uuid.UUID | None = Query(default=None, description="Filter by lead"),
    call_id: uuid.UUID | None = Query(default=None, description="Filter by call"),
    template_type: str | None = Query(
        default=None,
        description="tour_confirmation | follow_up | lead_response | general",
    ),
    delivery_status: str | None = Query(
        default=None,
        description="pending | sent | delivered | bounced | failed",
    ),
    date_from: str | None = Query(
        default=None,
        description="YYYY-MM-DD — inclusive start, filters on created_at",
    ),
    date_to: str | None = Query(
        default=None,
        description="YYYY-MM-DD — inclusive end, filters on created_at",
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[EmailListItem]:
    """Return a paginated email list filtered by property.

    Consumer Notes (Alex — dashboard):
        - Always pass `property_id` — cross-property email lists are not supported.
        - `date_from` / `date_to` filter on `created_at` (UTC date).
        - List items omit the email body (large text); call the detail endpoint
          to fetch it.
        - Results are ordered newest-first.

    Errors:
        400 INVALID_REQUEST    — date_from / date_to is not YYYY-MM-DD.
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/emails/?property_id=...&template_type=tour_confirmation&page=1
    """
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
        EmailRecord.property_id == property_id,
        EmailRecord.company_id == company_id,
    ]
    if lead_id is not None:
        filters.append(EmailRecord.lead_id == lead_id)
    if call_id is not None:
        filters.append(EmailRecord.call_id == call_id)
    if template_type is not None:
        filters.append(EmailRecord.template_type == template_type)
    if delivery_status is not None:
        filters.append(EmailRecord.delivery_status == delivery_status)
    if date_from is not None:
        try:
            filters.append(
                cast(EmailRecord.created_at, Date) >= date_type.fromisoformat(date_from)
            )
        except ValueError as exc:
            raise APIError(
                400,
                "INVALID_REQUEST",
                f"date_from must be YYYY-MM-DD, got: {date_from!r}",
            ) from exc
    if date_to is not None:
        try:
            filters.append(
                cast(EmailRecord.created_at, Date) <= date_type.fromisoformat(date_to)
            )
        except ValueError as exc:
            raise APIError(
                400,
                "INVALID_REQUEST",
                f"date_to must be YYYY-MM-DD, got: {date_to!r}",
            ) from exc

    total: int = (
        await db.scalar(select(func.count()).select_from(EmailRecord).where(*filters)) or 0
    )

    result = await db.execute(
        select(EmailRecord)
        .where(*filters)
        .order_by(EmailRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    emails = result.scalars().all()

    if total > 0:
        logger.info(
            "list_emails property_id=%s company_id=%s total=%d page=%d delivery_status=%s",
            property_id,
            company_id,
            total,
            page,
            delivery_status,
        )

    return PaginatedResponse[EmailListItem](
        items=[EmailListItem.model_validate(e) for e in emails],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /emails/{email_id}
# ---------------------------------------------------------------------------


@router.get("/{email_id}", response_model=EmailDetailResponse)
@limiter.limit("60/minute")
async def get_email(
    request: Request,
    email_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> EmailDetailResponse:
    """Return the full email record including subject + body.

    Consumer Notes (Alex — dashboard):
        - Body is plain text (or HTML — the agent decides per template).
        - Scope check is by `company_id` on the row itself.

    Errors:
        404 EMAIL_NOT_FOUND — email does not exist in company scope.

    Example:
        GET /v1/emails/a1b2c3d4-0000-0000-0000-000000000200
    """
    result = await db.execute(
        select(EmailRecord).where(
            EmailRecord.id == email_id,
            EmailRecord.company_id == company_id,
        )
    )
    email = result.scalar_one_or_none()
    if email is None:
        raise APIError(
            status_code=404,
            code="EMAIL_NOT_FOUND",
            message="Email not found or does not belong to this company.",
        )

    return EmailDetailResponse.model_validate(email)
