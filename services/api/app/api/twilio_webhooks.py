"""Twilio-facing webhook infrastructure for Waxwing Voice.

This module owns the three HTTP webhook endpoints that Twilio calls and the
WebSocket endpoint that bridges Twilio's Media Streams protocol to Akhil's
voice agent.

Endpoints (registered in main.py):
    POST /v1/voice/twilio          — inbound call handler; returns TwiML
    POST /v1/voice/status          — call status callback; returns 204
    POST /v1/voice/twilio/fallback — error fallback; returns TwiML
    WS   /ws/twilio/media          — real-time audio bridge (on app directly)

Auth: Twilio X-Twilio-Signature HMAC validation on all HTTP webhooks.
      Validation is skipped (with a warning) when TWILIO_AUTH_TOKEN is empty,
      which is the intended behaviour for local development without real Twilio.

Consumer Notes (Akhil — voice agent):
    - The WebSocket at /ws/twilio/media delivers mulaw audio in base64 chunks.
    - The TwiML <Stream> passes `call_id` (internal UUID) and `caller_phone`
      as custom parameters in the "start" event so the agent can associate the
      stream with the correct Call record.
    - The two TODO(akhil) markers in twilio_media_stream are the integration
      points: session initialisation on "start" and audio forwarding on "media".
"""

import json
import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request, Response, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.call import Call

logger = logging.getLogger(__name__)

router = APIRouter(tags=["twilio-webhooks"])

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_twilio_signature(request: Request, body: bytes) -> bool:
    """Return True if the X-Twilio-Signature header is valid.

    When TWILIO_AUTH_TOKEN is empty, validation is skipped and a warning is
    logged.  This allows local development without real Twilio credentials.

    Args:
        request: The incoming FastAPI request (used for URL and header).
        body: The raw request body bytes (must be read before calling this).

    Returns:
        True if the signature is valid or validation is intentionally skipped.
        False if the token is set but the signature does not match.
    """
    settings = get_settings()
    auth_token = settings.twilio_auth_token

    if not auth_token:
        logger.warning(
            "TWILIO_AUTH_TOKEN is not set — skipping Twilio signature validation. "
            "Set the token before deploying to production."
        )
        return True

    try:
        from twilio.request_validator import RequestValidator  # type: ignore[import-untyped]
    except ImportError:
        logger.warning(
            "twilio package is not installed — skipping signature validation. "
            "Run `uv add twilio` from services/api/ to enable it."
        )
        return True

    validator = RequestValidator(auth_token)
    signature = request.headers.get("X-Twilio-Signature", "")
    url = str(request.url)

    # Twilio validates against the exact URL it was configured with.
    # Parse the form body as a flat dict for the validator.
    try:
        from urllib.parse import parse_qsl

        params = dict(parse_qsl(body.decode("utf-8")))
    except Exception:
        params = {}

    return validator.validate(url, params, signature)


def _map_twilio_status(twilio_status: str, current_status: str) -> str:
    """Map a Twilio CallStatus value to the internal Call.status enum.

    Args:
        twilio_status: The value from Twilio's CallStatus form field.
        current_status: The call's existing internal status (fallback).

    Returns:
        One of "active", "completed", "abandoned".
    """
    mapping: dict[str, str] = {
        "completed": "completed",
        "failed": "abandoned",
        "busy": "abandoned",
        "no-answer": "abandoned",
        "canceled": "abandoned",
        "in-progress": "active",
        "ringing": "active",
        "queued": "active",
    }
    return mapping.get(twilio_status, current_status)


# ---------------------------------------------------------------------------
# POST /v1/voice/twilio — inbound call webhook
# ---------------------------------------------------------------------------


@router.post("/twilio", response_class=Response)
async def incoming_call(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Handle an inbound Twilio call and return TwiML to open a media stream.

    Twilio calls this endpoint when a new call arrives on the configured
    phone number.  The handler:
      1. Validates the X-Twilio-Signature header.
      2. Creates a Call record in the database.
      3. Returns TwiML that instructs Twilio to open a bidirectional media
         stream to /ws/twilio/media, passing the internal call_id and
         caller_phone as custom parameters.

    Auth: Twilio X-Twilio-Signature HMAC (skipped when auth token not set).

    Returns:
        TwiML XML response (application/xml) with Connect > Stream.

    Raises:
        HTTP 403 if the Twilio signature is invalid.

    Example TwiML produced:
        <Response>
          <Connect>
            <Stream url="wss://example.ngrok.io/ws/twilio/media">
              <Parameter name="call_id" value="<uuid>" />
              <Parameter name="caller_phone" value="+15125551234" />
            </Stream>
          </Connect>
        </Response>
    """
    body = await request.body()

    if not _validate_twilio_signature(request, body):
        logger.warning("Twilio signature validation failed for incoming_call")
        return Response(content="Forbidden", status_code=403)

    form = await request.form()
    call_sid: str = str(form.get("CallSid", ""))
    caller_phone: str = str(form.get("From", ""))
    called_to: str = str(form.get("To", ""))

    logger.info("Incoming call: CallSid=%s From=%s To=%s", call_sid, caller_phone, called_to)

    settings = get_settings()
    company_id = uuid.UUID(settings.default_company_id)
    property_id = uuid.UUID(settings.default_property_id)

    call = Call(
        property_id=property_id,
        company_id=company_id,
        twilio_call_sid=call_sid or None,
        caller_phone=caller_phone or None,
        started_at=datetime.now(UTC).isoformat(),
        status="active",
        # Medium 4 fix: set livekit_room_id now (= call UUID) so DB column is not NULL.
        # The bridge names the LiveKit room after the call UUID, so setting it here
        # means the DB and the bridge agree without needing a later UPDATE. Dashboard
        # joins on livekit_room_id will work immediately after call creation.
        # NOTE: call.id is set by the DB on flush, so this line runs AFTER flush below.
    )
    db.add(call)
    await db.flush()
    # Medium 4 fix (continued): set livekit_room_id after flush so call.id is available.
    call.livekit_room_id = str(call.id)

    logger.info("Created Call record: call_id=%s twilio_call_sid=%s", call.id, call_sid)

    # Build TwiML using the twilio helper library when available; fall back to
    # raw string construction so the endpoint still works during development
    # before `uv add twilio` has been run.
    try:
        from twilio.twiml.voice_response import (  # type: ignore[import-untyped]
            Connect,
            Stream,
            VoiceResponse,
        )

        response = VoiceResponse()
        connect = Connect()
        stream = Stream(
            url=f"wss://{settings.public_base_url.removeprefix('https://').removeprefix('http://')}/ws/twilio/media"
        )
        stream.parameter(name="call_id", value=str(call.id))
        stream.parameter(name="caller_phone", value=caller_phone)
        connect.append(stream)
        response.append(connect)
        twiml = str(response)
    except ImportError:
        # Fallback: hand-craft the TwiML string so local dev still works.
        ws_host = settings.public_base_url.removeprefix("https://").removeprefix("http://")
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="wss://{ws_host}/ws/twilio/media">'
            f'<Parameter name="call_id" value="{call.id}"/>'
            f'<Parameter name="caller_phone" value="{caller_phone}"/>'
            "</Stream>"
            "</Connect>"
            "</Response>"
        )

    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# POST /v1/voice/status — call status callback
# ---------------------------------------------------------------------------


@router.post("/status", response_class=Response)
async def call_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Update the Call record when Twilio reports a status change.

    Twilio posts to this endpoint throughout the call lifecycle (ringing,
    in-progress, completed, failed, etc.).  The handler maps the Twilio status
    to the internal enum and persists it, also recording ended_at and duration
    when the call terminates.

    Auth: Twilio X-Twilio-Signature HMAC (skipped when auth token not set).

    Returns:
        HTTP 204 No Content on success.
        HTTP 403 if the Twilio signature is invalid.
        HTTP 200 with empty body if the Call record is not found (Twilio may
        send status callbacks for calls that were not created through this
        handler — log and discard gracefully).

    Example form payload from Twilio:
        CallSid=CA123&CallStatus=completed&CallDuration=42&From=%2B15125551234&To=%2B15127770000
    """
    body = await request.body()

    if not _validate_twilio_signature(request, body):
        logger.warning("Twilio signature validation failed for call_status")
        return Response(content="Forbidden", status_code=403)

    form = await request.form()
    call_sid: str = str(form.get("CallSid", ""))
    twilio_status: str = str(form.get("CallStatus", ""))
    duration_raw: str = str(form.get("CallDuration", ""))

    logger.info(
        "Call status update: CallSid=%s CallStatus=%s Duration=%s",
        call_sid,
        twilio_status,
        duration_raw,
    )

    result = await db.execute(select(Call).where(Call.twilio_call_sid == call_sid))
    call = result.scalar_one_or_none()

    if call is None:
        logger.warning(
            "Received status callback for unknown CallSid=%s status=%s — ignoring",
            call_sid,
            twilio_status,
        )
        return Response(status_code=200)

    new_status = _map_twilio_status(twilio_status, call.status)
    call.status = new_status

    if new_status in ("completed", "abandoned"):
        call.ended_at = datetime.now(UTC).isoformat()
        if duration_raw.isdigit():
            call.duration = int(duration_raw)

    await db.flush()

    logger.info(
        "Updated Call call_id=%s status=%s->%s duration=%s",
        call.id,
        twilio_status,
        new_status,
        call.duration,
    )

    return Response(status_code=204)


# ---------------------------------------------------------------------------
# POST /v1/voice/twilio/fallback — error fallback webhook
# ---------------------------------------------------------------------------


@router.post("/twilio/fallback", response_class=Response)
async def incoming_call_fallback(request: Request) -> Response:
    """Return a graceful TwiML fallback when the primary webhook fails.

    Twilio calls the fallback URL if the primary webhook (POST /v1/voice/twilio)
    returns an error or times out.  No DB access or signature validation is
    performed here — the goal is maximum availability.

    Auth: None (fallback path must always respond).

    Returns:
        TwiML XML that reads a message to the caller and hangs up.

    Example TwiML produced:
        <Response>
          <Say>Sorry, we are having trouble connecting the assistant.
               A team member will follow up shortly.</Say>
          <Hangup />
        </Response>
    """
    logger.warning(
        "Twilio fallback webhook triggered — primary webhook may have failed. url=%s",
        str(request.url),
    )

    try:
        from twilio.twiml.voice_response import (  # type: ignore[import-untyped]
            Hangup,
            Say,
            VoiceResponse,
        )

        response = VoiceResponse()
        response.append(
            Say(
                "Sorry, we are having trouble connecting the assistant. "
                "A team member will follow up shortly."
            )
        )
        response.append(Hangup())
        twiml = str(response)
    except ImportError:
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Say>Sorry, we are having trouble connecting the assistant. "
            "A team member will follow up shortly.</Say>"
            "<Hangup/>"
            "</Response>"
        )

    return Response(content=twiml, media_type="application/xml")


# ---------------------------------------------------------------------------
# WebSocket /ws/twilio/media — real-time audio bridge
# ---------------------------------------------------------------------------


async def twilio_media_stream(websocket: WebSocket) -> None:
    """Bridge Twilio's bidirectional Media Streams protocol to the LiveKit room.

    Registered directly on the FastAPI `app` in main.py (not via APIRouter)
    because WebSocket routes do not propagate cleanly through include_router
    when the router has a URL prefix.

    Protocol reference: https://www.twilio.com/docs/voice/media-streams

    Message events handled:
        connected — stream established; logged for observability.
        start     — stream metadata (streamSid, callSid) plus custom parameters
                    injected by the TwiML (call_id, caller_phone). Initialises the
                    LiveKitBridge, which creates the room and publishes caller audio.
        media     — base64-encoded PCMU audio chunk from the caller; forwarded
                    to LiveKit via the bridge (mu-law decode → 16 kHz PCM → LiveKit).
        dtmf      — DTMF digit pressed by the caller; logged for observability.
        stop      — stream ended by Twilio; bridge is closed, room disconnected.

    Architecture (ADR-0004, ADR-0006):
        The bridge creates a per-call LiveKit room named by the call_id UUID.
        Room metadata contains: call_id, twilio_call_sid, property_id.
        The voice-agent worker (services/voice-agent/) connects to the same room,
        reads the metadata, and subscribes to the "caller-audio" track.

        Design choice (Akhil, 2026-05-09):
            The incoming_call webhook creates the Call record and passes call_id
            as a TwiML Stream parameter. The bridge reads call_id from the "start"
            event and sets it in the LiveKit room metadata. The voice-agent reads
            call_id from room metadata and does NOT create a second call record,
            eliminating the duplicate Call record bug (backend issue #3).

    Args:
        websocket: The FastAPI WebSocket connection accepted from Twilio.
    """
    await websocket.accept()

    settings = get_settings()
    call_id: str | None = None
    bridge = None

    # Only instantiate bridge if LiveKit is configured. If not configured (e.g.
    # local dev without LiveKit), log and continue in stub mode so the webhook
    # still accepts and logs Twilio events without crashing.
    if settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret:
        from app.bridge.livekit_bridge import LiveKitBridge
        bridge = LiveKitBridge(
            livekit_url=settings.livekit_url,
            livekit_api_key=settings.livekit_api_key,
            livekit_api_secret=settings.livekit_api_secret,
        )
    else:
        logger.warning(
            "LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET not configured — "
            "bridge running in stub mode. Set these env vars to enable real-time audio."
        )

    try:
        async for message in websocket.iter_text():
            data: dict = json.loads(message)
            event: str = data.get("event", "")

            if event == "connected":
                logger.info("Twilio media stream connected")

            elif event == "start":
                start_payload = data.get("start", {})
                stream_sid: str = start_payload.get("streamSid", "")
                # The actual Twilio CallSid (CA...) — different from the stream SID.
                twilio_call_sid: str = start_payload.get("callSid", "")
                custom_params: dict = start_payload.get("customParameters", {})
                call_id = custom_params.get("call_id")
                caller_phone: str | None = custom_params.get("caller_phone")

                logger.info(
                    "Stream started: stream_sid=%s call_id=%s twilio_call_sid=%s",
                    stream_sid,
                    call_id,
                    twilio_call_sid,
                )

                if bridge is not None and call_id:
                    await bridge.handle_start(
                        data,
                        websocket,
                        call_id=call_id,
                        property_id=settings.default_property_id,
                        caller_phone=caller_phone,
                    )
                elif bridge is not None and not call_id:
                    logger.warning(
                        "Stream start missing call_id in customParameters — "
                        "bridge will not connect. Check TwiML Stream parameter configuration."
                    )

            elif event == "media":
                if bridge is not None:
                    await bridge.handle_media(data)

            elif event == "dtmf":
                digit: str = data.get("dtmf", {}).get("digit", "")
                logger.info("DTMF received: digit=%s call_id=%s", digit, call_id)
                if bridge is not None:
                    await bridge.handle_dtmf(data)

            elif event == "stop":
                logger.info("Stream stopped for call_id=%s", call_id)
                if bridge is not None:
                    await bridge.close()
                    bridge = None

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for call_id=%s", call_id)
    except Exception:
        logger.exception("Error in media stream for call_id=%s", call_id)
    finally:
        logger.info("Media stream closed for call_id=%s", call_id)
        if bridge is not None:
            await bridge.close()
