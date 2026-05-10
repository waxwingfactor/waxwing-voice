"""AuditLog endpoints — consumed by Alex (admin / compliance views).

Auth: Bearer JWT (see app.database.get_company_id).

Endpoints:
    GET /v1/audit-logs/   -> paginated audit log list (filtered by property)

The AuditLog model carries `property_id` directly, so scoping per property
is a simple WHERE clause — no JOIN through entity_type/entity_id is needed.
For company-level audit entries (where `property_id` is NULL) the dashboard
should call without `property_id` once the route is extended; today the spec
requires `property_id`.
"""

import logging
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.audit_log import AuditLog
from app.models.property import Property
from app.schemas.audit_logs import AuditLogItem
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit-logs", tags=["audit-logs"])


# ---------------------------------------------------------------------------
# GET /audit-logs/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[AuditLogItem])
@limiter.limit("60/minute")
async def list_audit_logs(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    action: str | None = Query(
        default=None,
        description="SCREAMING_SNAKE_CASE action; e.g. TOUR_BOOKED, EMAIL_SENT",
    ),
    entity_type: str | None = Query(
        default=None,
        description="call | lead | booking | email_record | document | property",
    ),
    actor_type: str | None = Query(
        default=None,
        description="USER | SYSTEM | VOICE_AGENT",
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
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[AuditLogItem]:
    """Return a paginated audit-log list scoped to a single property.

    Consumer Notes (Alex — admin / compliance):
        - Default page_size is 50 (audit logs are higher-volume than calls/leads).
        - `property_id` is REQUIRED; logs with `property_id IS NULL` (company-
          level events such as PROPERTY_CREATED before the row exists) are NOT
          surfaced through this endpoint. Add a separate `/v1/audit-logs/company/`
          variant if/when that becomes necessary.
        - `entity_type` values mirror the writer code: call, lead, booking,
          email_record, document, property.
        - Results are ordered newest-first.

    Errors:
        400 INVALID_REQUEST    — date_from / date_to is not YYYY-MM-DD.
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/audit-logs/?property_id=...&action=TOUR_BOOKED&page=1
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
        AuditLog.company_id == company_id,
        AuditLog.property_id == property_id,
    ]
    if action is not None:
        filters.append(AuditLog.action == action)
    if entity_type is not None:
        filters.append(AuditLog.entity_type == entity_type)
    if actor_type is not None:
        filters.append(AuditLog.actor_type == actor_type)
    if date_from is not None:
        try:
            filters.append(
                cast(AuditLog.created_at, Date) >= date_type.fromisoformat(date_from)
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
                cast(AuditLog.created_at, Date) <= date_type.fromisoformat(date_to)
            )
        except ValueError as exc:
            raise APIError(
                400,
                "INVALID_REQUEST",
                f"date_to must be YYYY-MM-DD, got: {date_to!r}",
            ) from exc

    total: int = (
        await db.scalar(select(func.count()).select_from(AuditLog).where(*filters)) or 0
    )

    result = await db.execute(
        select(AuditLog)
        .where(*filters)
        .order_by(AuditLog.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = result.scalars().all()

    if total > 0:
        logger.info(
            "list_audit_logs property_id=%s company_id=%s total=%d page=%d action=%s",
            property_id,
            company_id,
            total,
            page,
            action,
        )

    return PaginatedResponse[AuditLogItem](
        items=[AuditLogItem.model_validate(r) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
    )
