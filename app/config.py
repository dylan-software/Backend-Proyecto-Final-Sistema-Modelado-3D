import secrets
from functools import lru_cache

from pydantic import field_validator
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
    jwt_secret: str = ""
    jwt_expire_minutes: int = 60

    @field_validator("jwt_secret")
    @classmethod
    def random_secret_if_empty(cls, value):
        # Without JWT_SECRET a random key is generated, so tokens stop working when the server restarts.
        return value or secrets.token_urlsafe(32)


@lru_cache
def get_settings() -> Settings:
    return Settings()
