"""
Application configuration loaded from environment variables.

All secrets come from environment variables — never hardcoded here.
Subbu will publish .env.example with the canonical list.

Phase 0: defines the shape; Phase 1 will add provider-specific validation.
"""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Runtime configuration for the voice agent service.

    Required for Phase 1 (marked with comments):
      - LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
      - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
      - GEMINI_API_KEY
      - WHISPER_MODEL (defaults to 'base' if not set)
      - VIBEVOICE_API_KEY
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # -----------------------------------------------------------------------
    # Backend API (Harsha's FastAPI service)
    # -----------------------------------------------------------------------
    backend_api_url: str = Field(
        default="http://localhost:8000",
        description="Base URL for Harsha's FastAPI backend. All durable actions route here.",
    )
    backend_api_timeout_seconds: float = Field(
        default=10.0,
        description="Per-request timeout for backend tool calls.",
    )

    # Service-level JWT for authenticating to Harsha's backend (Phase 5+).
    # Provisioned by Subbu via auth.create_access_token(company_id, expires_in)
    # and stored as the VOICE_AGENT_JWT environment variable. The token is an
    # HS256-signed JWT containing {"company_id": "<uuid>", "exp": <timestamp>}.
    # Default TTL from Harsha's create_access_token() is 24 hours. When the
    # token expires the backend returns 401; the service must be restarted (or
    # a token-refresh mechanism implemented) to get a fresh token.
    # Decision on minting strategy (offline vs. token endpoint vs. per-call)
    # is tracked in BLOCKERS.md §7 and needs a Subbu+Harsha decision before
    # the voice agent can run in staging.
    voice_agent_jwt: str = Field(
        default="",
        description=(
            "HS256 JWT for backend auth. Set VOICE_AGENT_JWT in env. "
            "Provisioned by Subbu via auth.create_access_token(). 24h TTL by default."
        ),
    )

    # -----------------------------------------------------------------------
    # LiveKit — Phase 1 required
    # -----------------------------------------------------------------------
    livekit_url: str | None = Field(
        default=None,
        description="wss://... LiveKit server URL. Required for Phase 1.",
    )
    livekit_api_key: str | None = Field(
        default=None,
        description="LiveKit API key. Required for Phase 1.",
    )
    livekit_api_secret: str | None = Field(
        default=None,
        description="LiveKit API secret. Required for Phase 1. Never log this.",
    )

    # -----------------------------------------------------------------------
    # Twilio — Phase 1 required
    # -----------------------------------------------------------------------
    twilio_account_sid: str | None = Field(
        default=None,
        description="Twilio Account SID. Required for Phase 1.",
    )
    twilio_auth_token: str | None = Field(
        default=None,
        description="Twilio Auth Token. Required for Phase 1. Never log this.",
    )

    # -----------------------------------------------------------------------
    # Gemini (LLM) — Phase 1 required
    # -----------------------------------------------------------------------
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key. Required for Phase 1. Never log this.",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description="Gemini model ID. Locked to Gemini-3.0 Flash family for MVP.",
    )

    # -----------------------------------------------------------------------
    # Whisper (STT) — Phase 1 required
    # -----------------------------------------------------------------------
    whisper_model: str = Field(
        default="base",
        description="Whisper model size: tiny/base/small/medium/large. Tune in Phase 5.",
    )
    whisper_device: str = Field(
        default="cpu",
        description="Device for Whisper inference: cpu or cuda.",
    )

    # -----------------------------------------------------------------------
    # VibeVoice (TTS) — Phase 1 required
    # -----------------------------------------------------------------------
    vibevoice_api_key: str | None = Field(
        default=None,
        description="VibeVoice API key. Required for Phase 1. Never log this.",
    )
    vibevoice_api_url: str = Field(
        default="https://api.vibevoice.ai",
        description="VibeVoice endpoint. Confirm with Subbu for staging vs prod.",
    )

    # -----------------------------------------------------------------------
    # Agent behavior
    # -----------------------------------------------------------------------
    default_property_id: str | None = Field(
        default=None,
        description=(
            "Fallback property ID for local testing. "
            "In production this comes from the Twilio call routing metadata."
        ),
    )
    silence_timeout_seconds: float = Field(
        default=3.0,
        description="Seconds of silence before agent considers the caller done speaking.",
    )
    max_response_tokens: int = Field(
        default=200,
        description=(
            "Soft cap on Gemini response tokens. Keep responses phone-length. "
            "Gemini will be instructed separately in the system prompt."
        ),
    )
    tool_retry_max_attempts: int = Field(
        default=2,
        description="Max retries for recoverable backend tool failures.",
    )

    # -----------------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------------
    log_level: str = Field(default="INFO", description="Logging level.")
    log_format: str = Field(
        default="json",
        description="'json' for structured production logs, 'console' for local dev.",
    )

    @field_validator("gemini_model")
    @classmethod
    def validate_gemini_model(cls, v: str) -> str:
        """Enforce locked stack — Gemini-3.0 Flash family only."""
        allowed_prefixes = ("gemini-2.0-flash", "gemini-1.5-flash")
        if not any(v.startswith(p) for p in allowed_prefixes):
            raise ValueError(
                f"gemini_model '{v}' is not in the approved Gemini Flash family. "
                "See docs/03-tooling-and-guardrails.md. File an ADR to change this."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings. Safe to call from anywhere."""
    return Settings()
