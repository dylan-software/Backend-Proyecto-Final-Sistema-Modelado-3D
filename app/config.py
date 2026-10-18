import secrets
from functools import lru_cache

from pydantic import Field, model_validator
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
    # "production" makes the server refuse to start without a fixed JWT_SECRET.
    environment: str = "development"
    # Frontend origins allowed by CORS, comma-separated (e.g. the Vercel URL and its previews).
    allowed_origins: str = "http://localhost:3000"
    # Registers GET /api/v1/spaces/debug-vars; off unless explicitly enabled.
    enable_debug_routes: bool = False
    # Most catalog pieces one account may store; POST /catalog answers 409 beyond it, before calling AI.
    max_catalog_items_per_user: int = Field(300, gt=0)
    # Calls to the AI model (chat, catalog, analyze, analyze-photo) per account; 429 beyond them.
    ai_requests_per_minute: int = Field(10, gt=0)
    ai_requests_per_day: int = Field(200, gt=0)
    # Failed logins allowed per email within the window; then 429 until the oldest failure expires.
    login_failures_per_email: int = Field(10, gt=0)
    login_failure_window_minutes: int = Field(15, gt=0)
    # Registrations per hour: in total, and per client IP (best effort, see app.utils.rate_limit).
    registrations_per_hour: int = Field(20, gt=0)
    registrations_per_ip_per_hour: int = Field(5, gt=0)
    # Proxies in front of the server that append to X-Forwarded-For; 0 ignores the header. Render: 1.
    trusted_proxy_hops: int = Field(0, ge=0)

    @property
    def is_production(self):
        return self.environment.strip().lower() == "production"

    @model_validator(mode="after")
    def random_secret_in_development(self):
        # Development only: a random key per start, so tokens stop working when the server restarts.
        # In production the secret stays empty and check_production_ready() stops the server.
        if not self.jwt_secret and not self.is_production:
            self.jwt_secret = secrets.token_urlsafe(32)
        return self

    def check_production_ready(self):
        """Raise if the configuration is unsafe to serve production traffic.

        A plain RuntimeError on purpose: a pydantic ValidationError would print every setting,
        including DATABASE_URL and the API keys, in the deploy log.
        """
        if self.is_production and not self.jwt_secret:
            raise RuntimeError("JWT_SECRET must be set when ENVIRONMENT=production; refusing to start.")

    @property
    def cors_origins(self):
        """ALLOWED_ORIGINS as a list, without blanks or trailing slashes (browsers send none)."""
        return [origin.strip().rstrip("/") for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.check_production_ready()
    return settings
