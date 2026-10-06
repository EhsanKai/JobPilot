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

    # Tailored documents (V0.8). A generation still "in progress" after this
    # many minutes (e.g. the server restarted mid-request) is treated as
    # failed, so the UI never waits on it forever and a new one may start.
    DOCUMENT_GENERATION_STALE_MINUTES: int = 10

    # Resume Intelligence limits. Text longer than this is rejected rather
    # than silently truncated (truncation would silently drop facts).
    RESUME_MAX_TEXT_CHARS: int = 60000
    # A processing run older than this is considered abandoned (e.g. the
    # server restarted mid-run) and may be restarted.
    RESUME_PROCESSING_STALE_MINUTES: int = 15

    # Job Discovery (V0.3). Both implemented sources use public, documented,
    # unauthenticated job-board APIs, so no credentials are needed. Sources
    # and the company boards they read are configuration, not code.
    #
    # Comma-separated source names to enable (see GET /jobs/sources).
    JOB_SOURCES_ENABLED: str = "greenhouse,lever"
    # Greenhouse board tokens: the `{token}` in job-boards.greenhouse.io/{token}.
    GREENHOUSE_BOARD_TOKENS: str = "getyourguide,hellofresh,grover"
    # Lever site names: the `{site}` in jobs.lever.co/{site}. Lever's API has
    # no company-name field, so an optional display name can be given as
    # `site:Display Name` (otherwise the site name is shown).
    LEVER_SITES: str = "ppro:PPRO,netlight:Netlight"
    # "global" (api.lever.co) or "eu" (api.eu.lever.co). A fixed choice, not
    # a URL, so configuration can't point the fetcher at arbitrary hosts.
    LEVER_INSTANCE: str = "global"
    JOB_SOURCE_TIMEOUT_SECONDS: float = 20.0
    # Safety cap on postings read from one board/site per run.
    JOB_SOURCE_MAX_JOBS_PER_BOARD: int = 1000
    # Maximum response size accepted from a source, in bytes.
    JOB_SOURCE_MAX_RESPONSE_BYTES: int = 25_000_000
    # Bounded retries for transient failures (connection errors, timeouts,
    # HTTP 429/500/502/503/504): extra attempts per request, and the base of
    # the exponential backoff in seconds (V0.4.1).
    JOB_SOURCE_MAX_RETRIES: int = 2
    JOB_SOURCE_RETRY_BACKOFF_SECONDS: float = 0.5
    # A discovery run older than this is considered abandoned (e.g. the server
    # restarted mid-run) and no longer blocks a new run.
    JOB_DISCOVERY_STALE_MINUTES: int = 10
    # Job Intelligence (V0.4): a source posting that no discovery run has
    # received for this many days is reported as "stale". Nothing is deleted.
    JOB_POSTING_STALE_DAYS: int = 14

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
