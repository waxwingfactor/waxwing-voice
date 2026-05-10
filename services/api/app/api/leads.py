"""Lead endpoints — consumed by Alex (dashboard).

Auth: X-Company-Id header (Phase 1 placeholder).

Endpoints:
    GET /v1/leads/           -> paginated lead list (filtered by property)
    GET /v1/leads/{lead_id}  -> full lead detail
"""

import logging
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.lead import Lead
from app.models.property import Property
from app.schemas.leads import LeadDetailResponse, LeadListItem
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/leads", tags=["leads"])


# ---------------------------------------------------------------------------
# GET /leads/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[LeadListItem])
@limiter.limit("60/minute")
async def list_leads(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    lead_score: str | None = Query(default=None, description="hot | warm | cold"),
    lead_status: str | None = Query(
        default=None,
        description="new | contacted | toured | applied | closed | lost",
    ),
    tour_interest: bool | None = Query(default=None, description="Filter by tour interest flag"),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Substring search across name, phone, email (case-insensitive)",
    ),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD — inclusive start"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD — inclusive end"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[LeadListItem]:
    """Return a paginated lead list filtered by property.

    Consumer Notes (Alex — dashboard):
        - Always pass `property_id` — cross-property lead lists are not supported.
        - `date_from` / `date_to` filter on `created_at` (UTC date).
        - `q` performs a case-insensitive SUBSTRING match across `name`, `phone`,
          and `email`. This is NOT a full-text search; for relevance-ranked
          full-text retrieval, Phase 5+ may add a Postgres `tsvector` index.
        - Results are ordered newest-first.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/leads/?property_id=...&lead_score=hot&q=jane&page=1
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
        Lead.property_id == property_id,
        Lead.company_id == company_id,
    ]
    if lead_score is not None:
        filters.append(Lead.lead_score == lead_score)
    if lead_status is not None:
        filters.append(Lead.lead_status == lead_status)
    if tour_interest is not None:
        filters.append(Lead.tour_interest == tour_interest)
    if q is not None and q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                Lead.name.ilike(pattern),
                Lead.phone.ilike(pattern),
                Lead.email.ilike(pattern),
            )
        )
    if date_from is not None:
        try:
            filters.append(cast(Lead.created_at, Date) >= date_type.fromisoformat(date_from))
        except ValueError as exc:
            raise APIError(
                400, "INVALID_REQUEST", f"date_from must be YYYY-MM-DD, got: {date_from!r}"
            ) from exc
    if date_to is not None:
        try:
            filters.append(cast(Lead.created_at, Date) <= date_type.fromisoformat(date_to))
        except ValueError as exc:
            raise APIError(
                400, "INVALID_REQUEST", f"date_to must be YYYY-MM-DD, got: {date_to!r}"
            ) from exc

    total: int = await db.scalar(select(func.count()).select_from(Lead).where(*filters)) or 0

    result = await db.execute(
        select(Lead)
        .where(*filters)
        .order_by(Lead.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    leads = result.scalars().all()

    if total > 0:
        logger.info(
            "list_leads property_id=%s company_id=%s total=%d page=%d q=%r",
            property_id,
            company_id,
            total,
            page,
            q,
        )

    return PaginatedResponse[LeadListItem](
        items=[LeadListItem.model_validate(lead) for lead in leads],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /leads/{lead_id}
# ---------------------------------------------------------------------------


@router.get("/{lead_id}", response_model=LeadDetailResponse)
async def get_lead(
    lead_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> LeadDetailResponse:
    """Return the full lead record.

    Consumer Notes (Alex — dashboard):
        - All lead qualification fields are returned, including `pet_info` (JSONB).
        - `call_id` is null for leads captured outside a voice call (future: web forms).

    Errors:
        404 LEAD_NOT_FOUND — lead does not exist in company scope.

    Example:
        GET /v1/leads/a1b2c3d4-0000-0000-0000-000000000099
        X-Company-Id: <company-uuid>
    """
    result = await db.execute(
        select(Lead).where(
            Lead.id == lead_id,
            Lead.company_id == company_id,
        )
    )
    lead = result.scalar_one_or_none()
    if lead is None:
        raise APIError(
            status_code=404,
            code="LEAD_NOT_FOUND",
            message="Lead not found or does not belong to this company.",
        )

    return LeadDetailResponse.model_validate(lead)
