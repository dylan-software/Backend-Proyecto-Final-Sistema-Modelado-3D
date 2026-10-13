from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Server configuration, read from environment variables and, if present, the .env file.

    Variable names are case-insensitive: DATABASE_URL fills database_url, and so on.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql://app:app@localhost:5432/app"
    omniroute_base_url: str = "http://localhost:20128/v1"
    omniroute_api_key: str = ""
    omniroute_model: str = ""
    omniroute_timeout: float = 60.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
