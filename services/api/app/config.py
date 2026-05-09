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
    embedding_dimensions: int = 1536


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    The @lru_cache ensures the .env file is read exactly once per process.
    In tests, call get_settings.cache_clear() between test cases if needed.
    """
    return Settings()
