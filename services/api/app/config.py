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


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    The @lru_cache ensures the .env file is read exactly once per process.
    In tests, call get_settings.cache_clear() between test cases if needed.
    """
    return Settings()
