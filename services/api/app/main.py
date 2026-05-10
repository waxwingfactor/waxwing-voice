"""FastAPI application entry point for the Waxwing Voice API.

Mount order:
    /v1/properties      -> app.api.properties (incl. PATCH for partial update)
    /v1/calls           -> app.api.calls
    /v1/voice           -> app.api.voice
    /v1/voice (Twilio)  -> app.api.twilio_webhooks (POST /twilio, /status, /twilio/fallback)
    /v1/leads           -> app.api.leads
    /v1/documents       -> app.api.documents (upload/list/detail/delete/reindex)
    /v1/auth            -> app.api.auth (login)
    /v1/integrations    -> app.api.integrations (calendar status)
    /v1/bookings        -> app.api.bookings
    /v1/emails          -> app.api.emails
    /v1/audit-logs      -> app.api.audit_logs
    /ws/twilio/media    -> app.api.twilio_webhooks.twilio_media_stream (WebSocket, no prefix)

Run locally:
    cd services/api && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api import (
    audit_logs,
    bookings,
    calls,
    documents,
    emails,
    integrations,
    leads,
    properties,
    twilio_webhooks,
    voice,
)
from app.api import auth as auth_router
from app.config import get_settings
from app.database import APIError
from app.limiter import limiter

settings = get_settings()

app = FastAPI(
    title="Waxwing Voice API",
    version="0.1.0",
    description=(
        "Backend API for Waxwing Voice — property data, voice tools, RAG, and workflow automation."
    ),
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(SlowAPIMiddleware)

# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Convert APIError into the canonical JSON error envelope.

    Shape: {"error": {"code": "...", "message": "...", "retryable": false}}
    Akhil's retry logic keys on `retryable` — never change this shape.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            }
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(properties.router, prefix="/v1")
app.include_router(calls.router, prefix="/v1")
app.include_router(voice.router, prefix="/v1")
app.include_router(twilio_webhooks.router, prefix="/v1/voice")
app.include_router(leads.router, prefix="/v1")
app.include_router(documents.router, prefix="/v1")

# === domain group A: properties + documents + auth + integrations ===
# (properties + documents are already mounted above; this group adds auth + integrations)
app.include_router(auth_router.router, prefix="/v1")
app.include_router(integrations.router, prefix="/v1")
# === end domain group A ===

# === domain group B: bookings + emails + audit logs ===
app.include_router(bookings.router, prefix="/v1")
app.include_router(emails.router, prefix="/v1")
app.include_router(audit_logs.router, prefix="/v1")
# === end domain group B ===


# ---------------------------------------------------------------------------
# WebSocket — Twilio Media Streams bridge
# Registered directly on `app` (not via include_router) because WebSocket
# routes do not propagate cleanly through a router that carries a URL prefix.
# ---------------------------------------------------------------------------


@app.websocket("/ws/twilio/media")
async def _twilio_media_stream_route(websocket: WebSocket) -> None:
    """Proxy to twilio_webhooks.twilio_media_stream — see that module for docs."""
    await twilio_webhooks.twilio_media_stream(websocket)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness check — returns 200 if the process is running.

    Does NOT verify DB connectivity. Use a separate readiness probe for that.
    """
    return {"status": "ok", "environment": settings.environment}
