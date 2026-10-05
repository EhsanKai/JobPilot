from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralized application configuration, sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"

    # Security
    SECRET_KEY: str = "insecure-dev-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ALGORITHM: str = "HS256"

    # CORS - comma separated list of allowed origins
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://jobpilot:jobpilot@localhost:5432/jobpilot"

    # File storage
    STORAGE_PROVIDER: str = "local"
    STORAGE_PATH: str = "./storage/resumes"
    MAX_RESUME_SIZE_MB: int = 10

    # AI provider (V0.2 Resume Intelligence). Credentials only ever come from
    # the environment. AI_PROVIDER="disabled" (or a missing API key) makes
    # resume analysis fail gracefully with AI_NOT_CONFIGURED instead of
    # crashing the app.
    AI_PROVIDER: str = "anthropic"
    ANTHROPIC_API_KEY: str | None = None
    AI_MODEL: str = "claude-sonnet-5-5"
    AI_TIMEOUT_SECONDS: float = 120.0
    AI_MAX_RETRIES: int = 2
    AI_MAX_OUTPUT_TOKENS: int = 16000

    # Resume Intelligence limits. Text longer than this is rejected rather
    # than silently truncated (truncation would silently drop facts).
    RESUME_MAX_TEXT_CHARS: int = 60000
    # A processing run older than this is considered abandoned (e.g. the
    # server restarted mid-run) and may be restarted.
    RESUME_PROCESSING_STALE_MINUTES: int = 15

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
