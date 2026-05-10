"""Call endpoints — consumed by Akhil (voice agent) and Alex (dashboard).

Auth: X-Company-Id header (Phase 1 placeholder).

Endpoints:
    POST  /v1/calls/           -> create a new call record
    PATCH /v1/calls/{call_id}  -> update call status / summary fields
    GET   /v1/calls/           -> paginated call list (filtered by property)
    GET   /v1/calls/{call_id}  -> full call detail with transcript + events
"""

import logging
import uuid
from datetime import date as date_type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import APIError, get_company_id, get_db
from app.limiter import limiter
from app.models.call import Call
from app.models.call_event import CallEvent
from app.models.property import Property
from app.models.transcript_segment import TranscriptSegment
from app.schemas.calls import (
    CallCreateRequest,
    CallCreateResponse,
    CallDetailResponse,
    CallListItem,
    CallUpdateRequest,
)
from app.schemas.calls import (
    CallEvent as CallEventSchema,
)
from app.schemas.calls import (
    TranscriptSegment as TranscriptSegmentSchema,
)
from app.schemas.pagination import PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/calls", tags=["calls"])


# ---------------------------------------------------------------------------
# POST /calls/
# ---------------------------------------------------------------------------


@router.post("/", response_model=CallCreateResponse, status_code=201)
async def create_call(
    body: CallCreateRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CallCreateResponse:
    """Create a new call record when an inbound call is received.

    Consumer Notes (Akhil — voice agent):
        - Call this immediately when a new call arrives from Twilio/LiveKit.
        - The returned `id` is the `call_id` used by all subsequent voice tool calls.
        - `twilio_call_sid` must be globally unique; duplicate inserts will fail
          with a DB constraint violation (not a retryable error).

    Idempotency: `twilio_call_sid` is a unique constraint — retrying with the
        same SID will produce a DB error. Callers should check before retrying.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        POST /v1/calls/
        {"property_id": "...", "twilio_call_sid": "CA...", "caller_phone": "+15125551234"}
    """
    # Verify property belongs to this company
    prop_result = await db.execute(
        select(Property).where(
            Property.id == body.property_id,
            Property.company_id == company_id,
        )
    )
    if prop_result.scalar_one_or_none() is None:
        raise APIError(
            status_code=404,
            code="PROPERTY_NOT_FOUND",
            message="Property not found or does not belong to this company.",
        )

    call = Call(
        property_id=body.property_id,
        company_id=company_id,
        twilio_call_sid=body.twilio_call_sid,
        livekit_room_id=body.livekit_room_id,
        caller_phone=body.caller_phone,
        started_at=body.started_at,
        status="active",
    )
    db.add(call)
    await db.flush()

    return CallCreateResponse.model_validate(call)


# ---------------------------------------------------------------------------
# PATCH /calls/{call_id}
# ---------------------------------------------------------------------------


@router.patch("/{call_id}", response_model=CallDetailResponse)
async def update_call(
    call_id: uuid.UUID,
    body: CallUpdateRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CallDetailResponse:
    """Partially update a call record (status, ended_at, escalation, etc.).

    Consumer Notes (Akhil — voice agent):
        - Call when the call ends to set `status`, `ended_at`, `duration`.
        - Escalation updates (`escalation_status`, `escalation_flag`) can be
          set here or via POST /v1/voice/request-handoff (which also writes an AuditLog).

    Errors:
        404 CALL_NOT_FOUND — call does not exist in company scope.

    Example:
        PATCH /v1/calls/{id}
        {"status": "completed", "ended_at": "2026-05-09T14:35:00Z", "duration": 183}
    """
    call = await _get_call_or_404(db, call_id, company_id)

    updatable = {
        "ended_at": body.ended_at,
        "duration": body.duration,
        "status": body.status,
        "primary_intent": body.primary_intent,
        "sentiment": body.sentiment,
        "escalation_status": body.escalation_status,
        "escalation_flag": body.escalation_flag,
    }
    for attr, value in updatable.items():
        if value is not None:
            setattr(call, attr, value)

    await db.flush()

    return CallDetailResponse(
        **{
            "id": call.id,
            "property_id": call.property_id,
            "company_id": call.company_id,
            "twilio_call_sid": call.twilio_call_sid,
            "livekit_room_id": call.livekit_room_id,
            "caller_phone": call.caller_phone,
            "started_at": call.started_at,
            "ended_at": call.ended_at,
            "duration": call.duration,
            "status": call.status,
            "primary_intent": call.primary_intent,
            "sentiment": call.sentiment,
            "escalation_status": call.escalation_status,
            "escalation_flag": call.escalation_flag,
            "summary": call.summary,
            "action_items": call.action_items,
            "next_steps": call.next_steps,
            "lead_fields_extracted": call.lead_fields_extracted,
            "created_at": call.created_at,
            "updated_at": call.updated_at,
            "transcript_segments": [],
            "call_events": [],
            "lead_id": None,
        }
    )


# ---------------------------------------------------------------------------
# GET /calls/
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedResponse[CallListItem])
@limiter.limit("60/minute")
async def list_calls(
    request: Request,
    property_id: uuid.UUID = Query(..., description="Filter by property (required)"),
    status: str | None = Query(default=None, description="Filter by call status"),
    q: str | None = Query(
        default=None,
        max_length=200,
        description="Substring search across caller_phone and summary (case-insensitive)",
    ),
    date_from: str | None = Query(default=None, description="YYYY-MM-DD — inclusive start"),
    date_to: str | None = Query(default=None, description="YYYY-MM-DD — inclusive end"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> PaginatedResponse[CallListItem]:
    """Return a paginated call list filtered by property.

    Consumer Notes (Alex — dashboard):
        - Always pass `property_id` — cross-property call lists are not supported.
        - `date_from` / `date_to` filter on `created_at` (UTC date).
        - `q` performs a case-insensitive SUBSTRING match across `caller_phone`
          and `summary`. This is NOT a full-text search; for relevance-ranked
          full-text retrieval, Phase 5+ may add a Postgres `tsvector` index.
        - Results are ordered newest-first.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.

    Example:
        GET /v1/calls/?property_id=...&status=completed&q=512&page=1
    """
    # Verify property belongs to this company
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
        Call.property_id == property_id,
        Call.company_id == company_id,
    ]
    if status is not None:
        filters.append(Call.status == status)
    if q is not None and q.strip():
        pattern = f"%{q.strip()}%"
        filters.append(
            or_(
                Call.caller_phone.ilike(pattern),
                Call.summary.ilike(pattern),
            )
        )
    if date_from is not None:
        try:
            filters.append(cast(Call.created_at, Date) >= date_type.fromisoformat(date_from))
        except ValueError as exc:
            raise APIError(
                400, "INVALID_REQUEST", f"date_from must be YYYY-MM-DD, got: {date_from!r}"
            ) from exc
    if date_to is not None:
        try:
            filters.append(cast(Call.created_at, Date) <= date_type.fromisoformat(date_to))
        except ValueError as exc:
            raise APIError(
                400, "INVALID_REQUEST", f"date_to must be YYYY-MM-DD, got: {date_to!r}"
            ) from exc

    total: int = await db.scalar(select(func.count()).select_from(Call).where(*filters)) or 0

    result = await db.execute(
        select(Call)
        .where(*filters)
        .order_by(Call.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    calls = result.scalars().all()

    if total > 0:
        logger.info(
            "list_calls property_id=%s company_id=%s total=%d page=%d q=%r",
            property_id,
            company_id,
            total,
            page,
            q,
        )

    return PaginatedResponse[CallListItem](
        items=[CallListItem.model_validate(c) for c in calls],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------------------------------------------------------------------------
# GET /calls/{call_id}
# ---------------------------------------------------------------------------


@router.get("/{call_id}", response_model=CallDetailResponse)
async def get_call(
    call_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CallDetailResponse:
    """Return the full call record including transcript segments and events.

    Consumer Notes (Alex — dashboard):
        - `transcript_segments` are ordered by `timestamp` (seconds since call start).
        - `call_events` are ordered by `created_at`.
        - `lead_id` is null until lead linking is implemented in Phase 2.

    Errors:
        404 CALL_NOT_FOUND — call does not exist in company scope.

    Example:
        GET /v1/calls/a1b2c3d4-0000-0000-0000-000000000002
        X-Company-Id: <company-uuid>
    """
    call = await _get_call_or_404(db, call_id, company_id)

    seg_result = await db.execute(
        select(TranscriptSegment)
        .where(TranscriptSegment.call_id == call_id)
        .order_by(TranscriptSegment.timestamp.asc())
    )
    segments = seg_result.scalars().all()

    event_result = await db.execute(
        select(CallEvent).where(CallEvent.call_id == call_id).order_by(CallEvent.created_at.asc())
    )
    events = event_result.scalars().all()

    return CallDetailResponse(
        id=call.id,
        property_id=call.property_id,
        company_id=call.company_id,
        twilio_call_sid=call.twilio_call_sid,
        livekit_room_id=call.livekit_room_id,
        caller_phone=call.caller_phone,
        started_at=call.started_at,
        ended_at=call.ended_at,
        duration=call.duration,
        status=call.status,
        primary_intent=call.primary_intent,
        sentiment=call.sentiment,
        escalation_status=call.escalation_status,
        escalation_flag=call.escalation_flag,
        summary=call.summary,
        action_items=call.action_items,
        next_steps=call.next_steps,
        lead_fields_extracted=call.lead_fields_extracted,
        created_at=call.created_at,
        updated_at=call.updated_at,
        transcript_segments=[TranscriptSegmentSchema.model_validate(s) for s in segments],
        call_events=[CallEventSchema.model_validate(e) for e in events],
        lead_id=None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_call_or_404(
    db: AsyncSession,
    call_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Call:
    """Fetch a call scoped to company_id; raise 404 CALL_NOT_FOUND if absent.

    Args:
        db: Active async session.
        call_id: UUID of the call to fetch.
        company_id: Company scope from request header.

    Returns:
        The ORM Call instance.

    Raises:
        APIError: 404 CALL_NOT_FOUND.
    """
    result = await db.execute(
        select(Call).where(
            Call.id == call_id,
            Call.company_id == company_id,
        )
    )
    call = result.scalar_one_or_none()
    if call is None:
        raise APIError(
            status_code=404,
            code="CALL_NOT_FOUND",
            message="Call not found or does not belong to this company.",
        )
    return call
