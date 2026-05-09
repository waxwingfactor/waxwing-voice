"""FastAPI application entry point for the Waxwing Voice API.

Mount order:
    /v1/properties  -> app.api.properties
    /v1/calls       -> app.api.calls
    /v1/voice       -> app.api.voice

Run locally:
    cd services/api && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import calls, properties, voice
from app.config import get_settings
from app.database import APIError

settings = get_settings()

app = FastAPI(
    title="Waxwing Voice API",
    version="0.1.0",
    description=(
        "Backend API for Waxwing Voice — property data, voice tools, RAG, and workflow automation."
    ),
)

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


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
async def health() -> dict:
    """Liveness check — returns 200 if the process is running.

    Does NOT verify DB connectivity. Use a separate readiness probe for that.
    """
    return {"status": "ok", "environment": settings.environment}
