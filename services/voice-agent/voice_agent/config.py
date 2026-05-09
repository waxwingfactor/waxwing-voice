"""
Application configuration loaded from environment variables.

All secrets come from environment variables — never hardcoded here.
See services/voice-agent/.env.example for the canonical variable list.

Stack decisions:
  - TTS: ElevenLabs Turbo v2.5 (ADR-0001, replaces VibeVoice)
  - Email: Resend via backend (ADR-0002, no voice-agent config needed)
  - Deployment: Local + Cloudflare Tunnel for MVP demo (ADR-0003)

Phase 0: defines the shape; Phase 1 adds provider validation.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Runtime configuration for the voice agent service.

    Required for Phase 1:
      - LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
      - TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
      - GEMINI_API_KEY
      - WHISPER_API_KEY (hosted OpenAI Whisper; provisioned by Subbu)
      - ELEVENLABS_API_KEY (replaces VIBEVOICE_API_KEY per ADR-0001)
      - ELEVENLABS_VOICE_ID (optional; defaults to Bella)
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
    # Hosted OpenAI Whisper. Subbu provisions the key; may share the same
    # OpenAI key used for embeddings (semantically distinct env var).
    # -----------------------------------------------------------------------
    whisper_api_key: str | None = Field(
        default=None,
        description=(
            "OpenAI API key for hosted Whisper. Required for Phase 1. "
            "Never log this. Set WHISPER_API_KEY in env. Provisioned by Subbu."
        ),
    )

    # -----------------------------------------------------------------------
    # ElevenLabs (TTS) — Phase 1 required (ADR-0001 replaces VibeVoice)
    # -----------------------------------------------------------------------
    elevenlabs_api_key: str | None = Field(
        default=None,
        description=(
            "ElevenLabs API key. Required for Phase 1. Never log this. "
            "Set ELEVENLABS_API_KEY in env. Provisioned by Subbu."
        ),
    )
    elevenlabs_voice_id: str = Field(
        default="EXAVITQu4vr4xnSDxMaL",
        description=(
            "ElevenLabs voice ID. Defaults to 'Bella' — phone-quality female voice "
            "recommended for Turbo v2.5. Override with ELEVENLABS_VOICE_ID env var."
        ),
    )
    tts_provider: Literal["elevenlabs", "mock"] = Field(
        default="elevenlabs",
        description=(
            "TTS provider selection. 'elevenlabs' uses ElevenLabsTTSAdapter (production). "
            "'mock' uses MockTTSAdapter (tests/local dev without API key). "
            "Set TTS_PROVIDER=mock in .env for local development without ElevenLabs credentials."
        ),
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
