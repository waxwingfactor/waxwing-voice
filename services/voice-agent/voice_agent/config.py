"""
Application configuration loaded from environment variables.

All secrets come from environment variables — never hardcoded here.
See services/voice-agent/.env.example for the canonical variable list.

Stack decisions (locked per docs/03-tooling-and-guardrails.md):
  - STT: Deepgram nova-2-phonecall (ADR-0005, replaces Whisper)
  - LLM: Gemini 2.0 Flash via livekit-plugins-google
  - TTS: ElevenLabs Turbo v2.5 (ADR-0001)
  - Email: Resend via backend (ADR-0002)
  - Deployment: Local + Cloudflare Tunnel for MVP (ADR-0003)
  - Orchestration: VoicePipelineAgent (ADR-0006)
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("voice_agent.config")


class Settings(BaseSettings):
    """
    Runtime configuration for the voice agent service.

    Required for production:
      - LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET
      - VOICE_AGENT_JWT  (HS256 JWT, provisioned by Subbu)
      - DEEPGRAM_API_KEY (ADR-0005)
      - GEMINI_API_KEY
      - ELEVENLABS_API_KEY
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
        description="Default per-request timeout for backend tool calls.",
    )

    # Service-level JWT for authenticating to Harsha's backend.
    # Provisioned by Subbu via auth.create_access_token(company_id, expires_in).
    # The token is an HS256-signed JWT containing {"company_id": "<uuid>", "exp": <ts>}.
    # Default TTL from create_access_token() is 24 hours.
    # Empty string is only valid in test mode (STT_PROVIDER=mock, LLM_PROVIDER=mock).
    voice_agent_jwt: str = Field(
        default="",
        description=(
            "HS256 JWT for backend auth. Set VOICE_AGENT_JWT in env. "
            "Provisioned by Subbu via auth.create_access_token(). 24h TTL by default. "
            "Empty is allowed for offline test mode but will fail at runtime."
        ),
    )

    # -----------------------------------------------------------------------
    # LiveKit — required for production
    # -----------------------------------------------------------------------
    livekit_url: str | None = Field(
        default=None,
        description="wss://... LiveKit server URL.",
    )
    livekit_api_key: str | None = Field(
        default=None,
        description="LiveKit API key.",
    )
    livekit_api_secret: str | None = Field(
        default=None,
        description="LiveKit API secret. Never log this value.",
    )

    # -----------------------------------------------------------------------
    # Twilio — used for caller ID context (caller_phone from room metadata)
    # -----------------------------------------------------------------------
    twilio_account_sid: str | None = Field(
        default=None,
        description="Twilio Account SID.",
    )
    twilio_auth_token: str | None = Field(
        default=None,
        description="Twilio Auth Token. Never log this.",
    )

    # -----------------------------------------------------------------------
    # Deepgram (STT) — ADR-0005, replaces Whisper
    # -----------------------------------------------------------------------
    deepgram_api_key: str | None = Field(
        default=None,
        description=(
            "Deepgram API key. Required for STT_PROVIDER=deepgram (default). "
            "Never log this. Set DEEPGRAM_API_KEY in env. Provisioned by Subbu."
        ),
    )

    # -----------------------------------------------------------------------
    # Gemini (LLM) — required for production
    # -----------------------------------------------------------------------
    gemini_api_key: str | None = Field(
        default=None,
        description="Google Gemini API key. Never log this.",
    )
    gemini_model: str = Field(
        default="gemini-2.0-flash",
        description=(
            "Gemini model ID. Locked to Gemini Flash family per locked stack. "
            "See docs/03-tooling-and-guardrails.md."
        ),
    )

    # -----------------------------------------------------------------------
    # ElevenLabs (TTS) — ADR-0001 replaces VibeVoice
    # -----------------------------------------------------------------------
    elevenlabs_api_key: str | None = Field(
        default=None,
        description="ElevenLabs API key. Never log this.",
    )
    elevenlabs_voice_id: str = Field(
        default="EXAVITQu4vr4xnSDxMaL",
        description="ElevenLabs voice ID. Defaults to 'Bella'.",
    )
    tts_provider: Literal["elevenlabs", "mock"] = Field(
        default="elevenlabs",
        description="TTS provider. 'mock' for offline tests.",
    )

    # -----------------------------------------------------------------------
    # STT provider selection — now includes deepgram (ADR-0005)
    # -----------------------------------------------------------------------
    stt_provider: Literal["deepgram", "whisper", "mock"] = Field(
        default="deepgram",
        description=(
            "STT provider. 'deepgram' is the locked production provider (ADR-0005). "
            "'whisper' retained for rollback path only. "
            "'mock' for offline tests."
        ),
    )

    # -----------------------------------------------------------------------
    # LLM provider selection
    # -----------------------------------------------------------------------
    llm_provider: Literal["gemini", "mock"] = Field(
        default="gemini",
        description="LLM provider. 'gemini' uses Gemini Flash. 'mock' for tests.",
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
        default=8.0,
        description=(
            "Seconds of silence before agent considers the caller done speaking. "
            "VoicePipelineAgent uses this via its VAD configuration."
        ),
    )
    max_response_tokens: int = Field(
        default=200,
        description=(
            "Soft cap on Gemini response tokens. Keep responses phone-length. "
            "Gemini is also instructed separately in the system prompt."
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

    # -----------------------------------------------------------------------
    # Validators
    # -----------------------------------------------------------------------

    @field_validator("gemini_model")
    @classmethod
    def validate_gemini_model(cls, v: str) -> str:
        """
        Enforce locked stack — Gemini Flash family only.

        Accepts:
          - gemini-2.0-flash (current default, ADR-0006)
          - gemini-1.5-flash (rollback path)
          - gemini-3.0-flash (forward-compatible for future release)
          - Any suffix after the model family prefix (e.g., gemini-2.0-flash-exp)
        """
        allowed_prefixes = ("gemini-2.0-flash", "gemini-1.5-flash", "gemini-3.0-flash")
        if not any(v.startswith(p) for p in allowed_prefixes):
            raise ValueError(
                f"gemini_model '{v}' is not in the approved Gemini Flash family. "
                "Allowed: gemini-2.0-flash, gemini-1.5-flash, gemini-3.0-flash (+ suffixes). "
                "See docs/03-tooling-and-guardrails.md. File an ADR to change this."
            )
        return v

    @model_validator(mode="after")
    def log_provider_config(self) -> "Settings":
        """
        Log which providers are configured at startup.
        Helps diagnose "silent 401" and missing-key problems quickly.
        Never logs key values — only whether keys are set or not.
        """
        log.info(
            "Voice agent configuration loaded",
            extra={
                "stt_provider": self.stt_provider,
                "llm_provider": self.llm_provider,
                "tts_provider": self.tts_provider,
                "deepgram_key_set": bool(self.deepgram_api_key),
                "gemini_key_set": bool(self.gemini_api_key),
                "elevenlabs_key_set": bool(self.elevenlabs_api_key),
                "voice_agent_jwt_set": bool(self.voice_agent_jwt),
                "livekit_url_set": bool(self.livekit_url),
                "gemini_model": self.gemini_model,
            },
        )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached singleton Settings. Safe to call from anywhere."""
    return Settings()
