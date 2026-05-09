"""Voice tool endpoints — consumed exclusively by Akhil's voice agent.

Auth: X-Company-Id header (Phase 1 placeholder).

All writes go through these endpoints — the voice agent never touches the DB directly.
Voice-facing errors include `suggested_action` context in the message where appropriate.

Endpoints:
    POST /v1/voice/search-knowledge     -> RAG retrieval (Phase 3: live pgvector)
    POST /v1/voice/leads                -> create or upsert a lead
    POST /v1/voice/events               -> record a call event
    POST /v1/voice/transcript-segment   -> append a transcript segment
    POST /v1/voice/call-summary         -> save call summary and AI fields
    POST /v1/voice/check-availability   -> tour slot availability (Phase 4: Google Calendar)
    POST /v1/voice/book-tour            -> create booking + Google Calendar event
    POST /v1/voice/send-email           -> send email via SendGrid and record it
    POST /v1/voice/request-handoff      -> escalate call and write audit log
"""

import uuid
from datetime import UTC, datetime, timedelta
from datetime import time as time_type

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import APIError, get_company_id, get_db
from app.integrations.email import send_sendgrid_email
from app.integrations.google_calendar import create_calendar_event, get_free_slots
from app.models.audit_log import AuditLog
from app.models.booking import Booking
from app.models.call import Call
from app.models.call_event import CallEvent
from app.models.email_record import EmailRecord
from app.models.lead import Lead
from app.models.property import Property
from app.models.transcript_segment import TranscriptSegment
from app.rag.embedder import embed_texts
from app.schemas.voice_tools import (
    AvailableSlot,
    BookTourRequest,
    BookTourResponse,
    CheckTourAvailabilityRequest,
    CheckTourAvailabilityResponse,
    CreateCallEventRequest,
    CreateCallEventResponse,
    CreateOrUpdateLeadRequest,
    CreateOrUpdateLeadResponse,
    KnowledgeResult,
    RequestHandoffRequest,
    RequestHandoffResponse,
    SaveCallSummaryRequest,
    SaveCallSummaryResponse,
    SaveTranscriptSegmentRequest,
    SaveTranscriptSegmentResponse,
    SearchKnowledgeRequest,
    SearchKnowledgeResponse,
    SendFollowUpEmailRequest,
    SendFollowUpEmailResponse,
)

router = APIRouter(prefix="/voice", tags=["voice"])


# ---------------------------------------------------------------------------
# 1. POST /voice/search-knowledge
# ---------------------------------------------------------------------------


@router.post("/search-knowledge", response_model=SearchKnowledgeResponse)
async def search_knowledge(
    body: SearchKnowledgeRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> SearchKnowledgeResponse:
    """Retrieve relevant knowledge chunks for a caller's question.

    Phase 1 STUB: always returns an empty results list. Real pgvector cosine
    similarity search is implemented in Phase 3.

    Consumer Notes (Akhil — voice agent):
        - Call before answering any property-specific question (pet policy, amenities, etc.).
        - results[].source_label should be read to callers as the citation.
        - top_k default is 5; increase only for complex multi-part questions.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.
    """
    await _get_property_or_404(db, body.property_id, company_id)

    settings = get_settings()
    api_key = settings.openai_api_key

    # Graceful degradation: if no API key is configured, return empty results
    # so the voice agent can still function without a knowledge base.
    if not api_key:
        return SearchKnowledgeResponse(
            property_id=body.property_id,
            query=body.query,
            results=[],
        )

    # Embed the query text — embed_texts returns [[float, ...]]
    vectors = await embed_texts([body.query], api_key)
    query_vec = vectors[0]

    # Format the vector as the "[0.1, 0.2, ...]" string expected by pgvector.
    query_vec_str = "[" + ",".join(str(v) for v in query_vec) + "]"

    # ⚠ Multi-tenant safety: WHERE clause always scopes by BOTH property_id AND
    # company_id to prevent cross-tenant data leakage.
    stmt = sa_text("""
        SELECT id, chunk_text, source_label, page_number,
               1 - (embedding <=> CAST(:query_vec AS vector)) AS similarity_score
        FROM knowledge_chunks
        WHERE property_id = CAST(:property_id AS uuid)
          AND company_id = CAST(:company_id AS uuid)
        ORDER BY embedding <=> CAST(:query_vec AS vector)
        LIMIT :top_k
    """)
    result = await db.execute(
        stmt,
        {
            "query_vec": query_vec_str,
            "property_id": str(body.property_id),
            "company_id": str(company_id),
            "top_k": body.top_k,
        },
    )
    rows = result.mappings().all()

    results = [
        KnowledgeResult(
            chunk_text=r["chunk_text"],
            source_label=r["source_label"],
            page_number=r["page_number"],
            similarity_score=max(0.0, min(1.0, float(r["similarity_score"]))),
        )
        for r in rows
    ]

    return SearchKnowledgeResponse(
        property_id=body.property_id,
        query=body.query,
        results=results,
    )


# ---------------------------------------------------------------------------
# 2. POST /voice/leads
# ---------------------------------------------------------------------------


@router.post("/leads", response_model=CreateOrUpdateLeadResponse)
async def create_or_update_lead(
    body: CreateOrUpdateLeadRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CreateOrUpdateLeadResponse:
    """Create or upsert a lead for a caller.

    Upsert key: (property_id, phone). If the caller has no phone number,
    a new lead row is always inserted (no dedup possible).

    Consumer Notes (Akhil — voice agent):
        - Call as soon as the caller's phone number is captured.
        - `created=True` means a fresh lead; `created=False` means the existing
          record was updated with new information.
        - Idempotent with the same (property_id, phone) — safe to retry.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.
    """
    await _get_property_or_404(db, body.property_id, company_id)

    fields = body.lead_fields
    lead_data: dict = {
        "property_id": body.property_id,
        "company_id": company_id,
        "call_id": body.call_id,
        "name": fields.name,
        "phone": fields.phone,
        "email": fields.email,
        "budget": fields.budget,
        "move_in_date": fields.move_in_date,
        "desired_unit_type": fields.desired_unit_type,
        "pet_info": fields.pet_info,
        "number_of_occupants": fields.number_of_occupants,
        "reason_for_moving": fields.reason_for_moving,
        "how_heard": fields.how_heard,
        "urgency": fields.urgency,
        "tour_interest": fields.tour_interest if fields.tour_interest is not None else False,
        "lead_score": fields.lead_score,
    }
    # Strip None values from upsert set to avoid overwriting existing data with nulls.
    # The conflict target columns (property_id, phone) are excluded from the update set.
    update_set = {
        k: v
        for k, v in lead_data.items()
        if k not in ("property_id", "company_id") and v is not None
    }

    if fields.phone is None:
        # No upsert key available — always insert a new row.
        new_id = uuid.uuid4()
        lead = Lead(**lead_data, id=new_id)
        db.add(lead)
        await db.flush()
        created = True
    else:
        # Pre-check existence so we can reliably report created vs updated.
        # This is safe under the unique constraint — a concurrent insert would
        # conflict and the upsert below would handle it.
        existing_result = await db.execute(
            select(Lead).where(
                Lead.property_id == body.property_id,
                Lead.phone == fields.phone,
            )
        )
        pre_existing = existing_result.scalar_one_or_none()
        created = pre_existing is None

        # INSERT ... ON CONFLICT (property_id, phone) DO UPDATE SET ...
        new_id = uuid.uuid4()
        stmt = (
            pg_insert(Lead)
            .values(id=new_id, **lead_data)
            .on_conflict_do_update(
                constraint="uq_leads_property_phone",
                set_=update_set,
            )
            .returning(Lead)
        )
        result = await db.execute(stmt)
        lead = result.scalar_one()

    await db.flush()

    return CreateOrUpdateLeadResponse(
        lead_id=lead.id,
        property_id=body.property_id,
        call_id=body.call_id,
        created=created,
    )


# ---------------------------------------------------------------------------
# 3. POST /voice/events
# ---------------------------------------------------------------------------


@router.post("/events", response_model=CreateCallEventResponse)
async def create_call_event(
    body: CreateCallEventRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CreateCallEventResponse:
    """Record a structured event that occurred during a call.

    Consumer Notes (Akhil — voice agent):
        - Fire for every significant event: call_started, lead_captured,
          tour_booked, escalated, tool_failed, etc.
        - `payload` must NOT contain PII — use IDs (lead_id, booking_id), not names.
        - `occurred_at` must be ISO 8601; the voice agent should emit this from
          its own clock, not rely on server time, for accurate timeline ordering.

    Errors:
        404 CALL_NOT_FOUND — call_id not in company scope.
    """
    await _get_call_or_404(db, body.call_id, company_id)

    event = CallEvent(
        call_id=body.call_id,
        event_type=body.event_type.value,
        payload=body.payload,
        occurred_at=body.occurred_at,
    )
    db.add(event)
    await db.flush()

    return CreateCallEventResponse(event_id=event.id, call_id=event.call_id)


# ---------------------------------------------------------------------------
# 4. POST /voice/transcript-segment
# ---------------------------------------------------------------------------


@router.post("/transcript-segment", response_model=SaveTranscriptSegmentResponse)
async def save_transcript_segment(
    body: SaveTranscriptSegmentRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> SaveTranscriptSegmentResponse:
    """Append a single speaker-turn transcript segment to a call.

    Consumer Notes (Akhil — voice agent):
        - Send in near-real-time as utterances are finalized by the STT provider.
        - `timestamp` is seconds since call start (from LiveKit/Whisper).
        - `speaker` must be "agent" or "caller".
        - Segments are append-only — no updates.

    Errors:
        404 CALL_NOT_FOUND — call_id not in company scope.
    """
    await _get_call_or_404(db, body.call_id, company_id)

    seg = TranscriptSegment(
        call_id=body.call_id,
        speaker=body.speaker,
        text=body.text,
        timestamp=body.timestamp,
    )
    db.add(seg)
    await db.flush()

    return SaveTranscriptSegmentResponse(segment_id=seg.id, call_id=seg.call_id)


# ---------------------------------------------------------------------------
# 5. POST /voice/call-summary
# ---------------------------------------------------------------------------


@router.post("/call-summary", response_model=SaveCallSummaryResponse)
async def save_call_summary(
    body: SaveCallSummaryRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> SaveCallSummaryResponse:
    """Persist the AI-generated call summary and extracted fields.

    Consumer Notes (Akhil — voice agent):
        - Call once after the call ends, before closing the session.
        - `action_items` should follow the "[CATEGORY] description" convention.
        - `lead_fields_extracted` is a snapshot — it will not overwrite the
          Lead record. Use POST /v1/voice/leads to update the lead.
        - Idempotent: calling again with the same call_id overwrites the fields.

    Errors:
        404 CALL_NOT_FOUND — call_id not in company scope.
    """
    call = await _get_call_or_404(db, body.call_id, company_id)

    call.summary = body.summary
    call.primary_intent = body.primary_intent
    call.sentiment = body.sentiment
    call.action_items = body.action_items
    call.escalation_flag = body.escalation_flag
    call.lead_fields_extracted = body.lead_fields_extracted
    call.next_steps = body.next_steps

    await db.flush()

    return SaveCallSummaryResponse(call_id=body.call_id, summary_saved=True)


# ---------------------------------------------------------------------------
# 6. POST /voice/check-availability
# ---------------------------------------------------------------------------


@router.post("/check-availability", response_model=CheckTourAvailabilityResponse)
async def check_tour_availability(
    body: CheckTourAvailabilityRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> CheckTourAvailabilityResponse:
    """Return available tour slots for a date range.

    Phase 4: queries Google Calendar freebusy API via the service account. Falls
    back to 3 hardcoded stub slots if Google Calendar is not configured or the
    API call fails, so the voice agent is never completely broken.

    Consumer Notes (Akhil — voice agent):
        - Present `available_slots` to the caller as options.
        - Use `slot_id` when calling POST /v1/voice/book-tour.
        - Live slot_ids have the format "gcal-{calendar_prefix}-{date}-{HHMM}".
        - Fallback stub slot_ids have the format "stub-{date}-{HH:MM}".

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.
    """
    await _get_property_or_404(db, body.property_id, company_id)

    settings = get_settings()

    try:
        raw_slots = await get_free_slots(
            calendar_id=settings.google_calendar_id,
            service_account_path=settings.google_service_account_path,
            start_date=body.date_range.start_date,
            end_date=body.date_range.end_date,
        )
        slots = [
            AvailableSlot(
                date=s["date"],
                start_time=s["start_time"],
                end_time=s["end_time"],
                slot_id=s["slot_id"],
            )
            for s in raw_slots
        ]
    except Exception:
        # Fall back to 3 stub slots so the voice agent is never completely broken.
        start = body.date_range.start_date
        next_day = start + timedelta(days=1)
        slots = [
            AvailableSlot(
                date=start,
                start_time=time_type(10, 0),
                end_time=time_type(10, 30),
                slot_id=f"stub-{start}-10:00",
            ),
            AvailableSlot(
                date=start,
                start_time=time_type(14, 0),
                end_time=time_type(14, 30),
                slot_id=f"stub-{start}-14:00",
            ),
            AvailableSlot(
                date=next_day,
                start_time=time_type(10, 0),
                end_time=time_type(10, 30),
                slot_id=f"stub-{next_day}-10:00",
            ),
        ]

    return CheckTourAvailabilityResponse(
        property_id=body.property_id,
        available_slots=slots,
    )


# ---------------------------------------------------------------------------
# 7. POST /voice/book-tour
# ---------------------------------------------------------------------------


@router.post("/book-tour", response_model=BookTourResponse)
async def book_tour(
    body: BookTourRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> BookTourResponse:
    """Create a booking for a tour slot and attempt to create a Google Calendar event.

    Phase 4: attempts real Google Calendar event creation. If Google Calendar is
    not configured or the API call fails, the booking record is still created with
    calendar_event_id=None — the booking is not rolled back.

    Audit log written: action=TOUR_BOOKED, entity_type=booking.

    Consumer Notes (Akhil — voice agent):
        - Always call create_or_update_lead before this to get a lead_id.
        - When calendar_event_id is present, a calendar invite was sent to the
          lead's email address.
        - When calendar_event_id is null, inform the caller a confirmation email
          will follow separately.
        - Suggested caller message: "I've booked your tour for {date} at {time}.
          You'll receive a confirmation shortly."

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.
        404 LEAD_NOT_FOUND     — lead_id not in company scope.
    """
    await _get_property_or_404(db, body.property_id, company_id)
    lead = await _get_lead_or_404(db, body.lead_id, company_id)
    settings = get_settings()

    # Attempt real Google Calendar event creation.
    # Failure is non-fatal — the booking record is always created.
    calendar_event_id: str | None = None
    try:
        start_dt = datetime.combine(
            body.selected_slot.date, body.selected_slot.start_time, tzinfo=UTC
        )
        end_dt = datetime.combine(body.selected_slot.date, body.selected_slot.end_time, tzinfo=UTC)
        summary = f"Property Tour — {body.tour_type.replace('_', ' ').title()}"
        calendar_event_id = await create_calendar_event(
            calendar_id=settings.google_calendar_id,
            service_account_path=settings.google_service_account_path,
            summary=summary,
            start_dt=start_dt,
            end_dt=end_dt,
            attendee_email=lead.email,
        )
    except Exception:
        calendar_event_id = None  # booking still created without a calendar event

    booking = Booking(
        lead_id=lead.id,
        property_id=body.property_id,
        company_id=company_id,
        call_id=body.call_id,
        tour_date=body.selected_slot.date,
        start_time=body.selected_slot.start_time,
        end_time=body.selected_slot.end_time,
        tour_type=body.tour_type,
        status="confirmed",
        calendar_event_id=calendar_event_id,
    )
    db.add(booking)
    await db.flush()

    audit = AuditLog(
        company_id=company_id,
        property_id=body.property_id,
        actor_type="VOICE_AGENT",
        actor_id="voice_agent",
        action="TOUR_BOOKED",
        entity_type="booking",
        entity_id=str(booking.id),
        metadata_={
            "lead_id": str(lead.id),
            "tour_date": str(body.selected_slot.date),
            "tour_type": body.tour_type,
            "calendar_event_id": calendar_event_id,
        },
    )
    db.add(audit)
    await db.flush()

    return BookTourResponse(
        booking_id=booking.id,
        calendar_event_id=calendar_event_id,
        tour_date=booking.tour_date,
        start_time=booking.start_time,
        status="confirmed",
    )


# ---------------------------------------------------------------------------
# 8. POST /voice/send-email
# ---------------------------------------------------------------------------


@router.post("/send-email", response_model=SendFollowUpEmailResponse)
async def send_follow_up_email(
    body: SendFollowUpEmailRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> SendFollowUpEmailResponse:
    """Send a follow-up email to a lead via SendGrid and record the result.

    Phase 4: dispatches the email synchronously via SendGrid before persisting
    the EmailRecord. delivery_status reflects the actual send outcome:
      - "sent"    — SendGrid accepted the message (202).
      - "failed"  — SendGrid rejected it or credentials are not configured.
      - "pending" — lead has no email address on file.

    Audit log written: action=EMAIL_SENT, entity_type=email_record.

    Consumer Notes (Akhil — voice agent):
        - Call after booking a tour to send the confirmation email.
        - When delivery_status="failed", tell the caller: "I wasn't able to send
          the confirmation email. Please check your email later or call us back."
        - When delivery_status="pending", the lead has no email — prompt for one
          before calling this endpoint.
        - recipient will be an empty string if the lead has no email on file.

    Errors:
        404 PROPERTY_NOT_FOUND — property_id not in company scope.
        404 LEAD_NOT_FOUND     — lead_id not in company scope.
    """
    prop = await _get_property_or_404(db, body.property_id, company_id)
    lead = await _get_lead_or_404(db, body.lead_id, company_id)
    settings = get_settings()

    subject = _build_subject(body.template_type.value, prop.name, body.context)
    body_text = _build_body(body.template_type.value, body.context)

    # Attempt real SendGrid dispatch if the lead has an email address.
    delivery_status = "pending"
    if lead.email:
        sent = await send_sendgrid_email(
            to_email=lead.email,
            subject=subject,
            body=body_text,
            api_key=settings.sendgrid_api_key,
            from_email=settings.sendgrid_from_email,
        )
        delivery_status = "sent" if sent else "failed"

    record = EmailRecord(
        property_id=body.property_id,
        company_id=company_id,
        call_id=body.call_id,
        lead_id=lead.id,
        recipient=lead.email or "",
        subject=subject,
        body=body_text,
        template_type=body.template_type.value,
        delivery_provider="sendgrid" if settings.sendgrid_api_key else None,
        delivery_status=delivery_status,
    )
    db.add(record)
    await db.flush()

    audit = AuditLog(
        company_id=company_id,
        property_id=body.property_id,
        actor_type="VOICE_AGENT",
        actor_id="voice_agent",
        action="EMAIL_SENT",
        entity_type="email_record",
        entity_id=str(record.id),
        metadata_={
            "lead_id": str(lead.id),
            "template_type": body.template_type.value,
            "delivery_status": delivery_status,
        },
    )
    db.add(audit)
    await db.flush()

    return SendFollowUpEmailResponse(
        email_id=record.id,
        recipient=record.recipient,
        subject=record.subject,
        delivery_status=record.delivery_status,
    )


# ---------------------------------------------------------------------------
# 9. POST /voice/request-handoff
# ---------------------------------------------------------------------------


@router.post("/request-handoff", response_model=RequestHandoffResponse)
async def request_handoff(
    body: RequestHandoffRequest,
    db: AsyncSession = Depends(get_db),
    company_id: uuid.UUID = Depends(get_company_id),
) -> RequestHandoffResponse:
    """Escalate a call to a human agent and write an audit log entry.

    Consumer Notes (Akhil — voice agent):
        - Call when the caller requests a human, when urgency="emergency",
          or when the voice agent cannot resolve the issue.
        - After calling this endpoint, the voice agent should inform the caller:
          "I'm connecting you with a team member now. Please hold."
        - `notification_sent=False` in Phase 1 — real notification (SMS/Slack)
          is Phase 4.

    Audit log written: action=HANDOFF_REQUESTED, entity_type=call.

    Errors:
        404 CALL_NOT_FOUND — call_id not in company scope.
    """
    call = await _get_call_or_404(db, body.call_id, company_id)

    call.escalation_status = "escalated"
    call.escalation_flag = True
    await db.flush()

    audit = AuditLog(
        company_id=company_id,
        property_id=body.property_id,
        actor_type="VOICE_AGENT",
        actor_id="voice_agent",
        action="HANDOFF_REQUESTED",
        entity_type="call",
        entity_id=str(body.call_id),
        metadata_={
            "reason": body.reason,
            "urgency": body.urgency.value,
            "lead_id": str(body.lead_id) if body.lead_id else None,
        },
    )
    db.add(audit)
    await db.flush()

    return RequestHandoffResponse(
        handoff_id=audit.id,
        call_id=body.call_id,
        status="requested",
        notification_sent=False,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_property_or_404(
    db: AsyncSession,
    property_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Property:
    """Fetch property scoped to company; raise 404 PROPERTY_NOT_FOUND if absent.

    Args:
        db: Active async session.
        property_id: Target property UUID.
        company_id: Company scope from request header.

    Returns:
        ORM Property instance.

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
            message=(
                "Property not found or does not belong to this company. "
                "Verify the property_id is correct for this account."
            ),
            suggested_action="Check the property_id in the voice agent configuration.",
        )
    return prop


async def _get_call_or_404(
    db: AsyncSession,
    call_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Call:
    """Fetch call scoped to company; raise 404 CALL_NOT_FOUND if absent.

    Args:
        db: Active async session.
        call_id: Target call UUID.
        company_id: Company scope from request header.

    Returns:
        ORM Call instance.

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
            message=(
                "Call not found or does not belong to this company. "
                "Ensure POST /v1/calls/ was called at the start of this session."
            ),
            suggested_action="Retry creating the call record via POST /v1/calls/ first.",
        )
    return call


async def _get_lead_or_404(
    db: AsyncSession,
    lead_id: uuid.UUID,
    company_id: uuid.UUID,
) -> Lead:
    """Fetch lead scoped to company; raise 404 LEAD_NOT_FOUND if absent.

    Args:
        db: Active async session.
        lead_id: Target lead UUID.
        company_id: Company scope from request header.

    Returns:
        ORM Lead instance.

    Raises:
        APIError: 404 LEAD_NOT_FOUND.
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
            message=(
                "Lead not found or does not belong to this company. "
                "Call POST /v1/voice/leads to create the lead first."
            ),
            suggested_action="Call create_or_update_lead before booking a tour or sending an email.",
        )
    return lead


def _build_subject(template_type: str, property_name: str, context: dict) -> str:
    """Generate a stub email subject from the template type.

    Args:
        template_type: One of tour_confirmation | follow_up | lead_response | general.
        property_name: Property name for the subject line.
        context: Template variables from the request.

    Returns:
        Subject string (max 998 chars per RFC 5322).
    """
    subjects = {
        "tour_confirmation": f"Tour Confirmation — {property_name}",
        "follow_up": f"Following Up — {property_name}",
        "lead_response": f"Thank You for Your Interest — {property_name}",
        "general": f"Message from {property_name}",
    }
    return subjects.get(template_type, f"Message from {property_name}")


def _build_body(template_type: str, context: dict) -> str:
    """Generate a minimal stub email body from template variables.

    Phase 4 will replace this with real template rendering.

    Args:
        template_type: Template identifier.
        context: Key-value template variables.

    Returns:
        Plain-text email body string.
    """
    if template_type == "tour_confirmation":
        tour_date = context.get("tour_date", "the scheduled date")
        tour_time = context.get("tour_time", "the scheduled time")
        return (
            f"Thank you for scheduling a tour! "
            f"Your tour is confirmed for {tour_date} at {tour_time}. "
            f"We look forward to seeing you."
        )
    if template_type == "follow_up":
        return (
            "Thank you for your interest in our community. "
            "We wanted to follow up and answer any questions you may have."
        )
    if template_type == "lead_response":
        return (
            "Thank you for reaching out! "
            "A member of our leasing team will be in touch with you shortly."
        )
    return "Thank you for contacting us. We'll be in touch soon."
