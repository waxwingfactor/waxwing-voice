"""Application settings loaded from environment variables via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str
    environment: str = "local"
    secret_key: str = "dev-secret-key"
    jwt_algorithm: str = "HS256"
    embedding_dimensions: int = 1536
    # Loaded from OPENAI_API_KEY env var / .env — no default so it is never hardcoded in source.
    openai_api_key: str = ""

    # Google Calendar integration (Phase 4)
    # GOOGLE_SERVICE_ACCOUNT_PATH — path to the service account JSON file on disk
    # GOOGLE_CALENDAR_ID — the calendar ID (e.g. primary or a specific calendar email)
    google_service_account_path: str = ""
    google_calendar_id: str = ""

    # Resend integration (Phase 4 — swapped from SendGrid)
    # RESEND_API_KEY — Resend API key (starts with "re_")
    # RESEND_FROM_EMAIL — verified sender address registered in Resend
    resend_api_key: str = ""
    resend_from_email: str = ""

    # Twilio integration (Phase 5 — inbound call webhooks and media streaming)
    # TWILIO_ACCOUNT_SID — Twilio Account SID (starts with "AC")
    # TWILIO_AUTH_TOKEN — used to validate X-Twilio-Signature on incoming webhooks
    # PUBLIC_BASE_URL — publicly reachable base URL for this service (e.g. ngrok URL)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    public_base_url: str = "http://localhost:8000"

    # Default scoping IDs used by Twilio webhooks which have no JWT context.
    # These match the seed data inserted by Alembic's initial migration.
    default_company_id: str = "00000000-0000-0000-0000-000000000001"
    default_property_id: str = "00000000-0000-0000-0000-000000000003"

    # LiveKit — used by the Twilio→LiveKit bridge (services/api/app/bridge/).
    # LIVEKIT_URL         — wss://... server URL
    # LIVEKIT_API_KEY     — LiveKit API key
    # LIVEKIT_API_SECRET  — LiveKit API secret (never log this value)
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    The @lru_cache ensures the .env file is read exactly once per process.
    In tests, call get_settings.cache_clear() between test cases if needed.
    """
    return Settings()
