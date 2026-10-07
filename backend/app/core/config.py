from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that are only ever acceptable outside production: the shipped defaults and the
# placeholders in .env.example / docker-compose.yml.
_DEV_SECRET_KEYS = frozenset(
    {
        "insecure-dev-secret-change-me",
        "change-me-to-a-long-random-string",
        "dev-only-insecure-secret-key-change-me",
    }
)
_DEV_DATABASE_CREDENTIALS = "jobpilot:jobpilot@"
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    """Centralized application configuration, sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # development: local work (default). test: the automated test suites. production: any
    # deployment reachable by real users -- startup refuses development secrets, default
    # database credentials, wildcard CORS and private-network browser access there.
    # "Manual testing" is not an environment: it is development plus the synthetic seed
    # data (docker compose --profile manual-test), which refuses to run in production.
    APP_ENV: Literal["development", "test", "production"] = "development"

    # Security
    SECRET_KEY: str = "insecure-dev-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    JWT_ALGORITHM: str = "HS256"
    # Login / registration throttling (V0.13): failed sign-ins allowed per account and per
    # client address within the window before further attempts get HTTP 429.
    LOGIN_MAX_FAILED_ATTEMPTS: int = 5
    LOGIN_MAX_ATTEMPTS_PER_IP: int = 30
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 900
    REGISTRATION_MAX_PER_IP: int = 10
    # Only when the API sits behind a reverse proxy you control: take the client address
    # for rate limiting from X-Forwarded-For. Otherwise anyone could spoof it.
    TRUST_PROXY_HEADERS: bool = False
    # Strict-Transport-Security on API responses. Default: on in production only (the
    # API must then be served over HTTPS; browsers ignore HSTS over plain HTTP anyway).
    SECURITY_HSTS_ENABLED: bool | None = None
    # Interactive API docs (/docs, /redoc, /openapi.json). Default: off in production.
    API_DOCS_ENABLED: bool | None = None

    # CORS - comma separated list of allowed origins. Never "*".
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"

    # Database
    DATABASE_URL: str = "postgresql+psycopg://jobpilot:jobpilot@localhost:5432/jobpilot"
    # How long `python -m app.db.migrate` waits for the database at container start.
    DB_STARTUP_TIMEOUT_SECONDS: float = 60.0

    # File storage
    STORAGE_PROVIDER: str = "local"
    STORAGE_PATH: str = "./storage/resumes"
    MAX_RESUME_SIZE_MB: int = 10
    # The user's own photo for a tailored CV (V0.9).
    MAX_PHOTO_SIZE_MB: int = 2
    # Upper bound for any request body, checked before the body is read (V0.13). Must stay
    # above the largest upload limit plus multipart overhead.
    MAX_REQUEST_BODY_MB: int = 15

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
    # V0.13: whether new accounts start with external AI processing allowed (each user can
    # change it in Settings > Privacy at any time). Whether this must be an opt-in is a
    # legal decision (docs/privacy.md); set false to make it one.
    AI_PROCESSING_DEFAULT_ALLOWED: bool = True
    # Pseudonymize e-mail addresses and phone numbers in resume text before it is sent to
    # the AI provider; the parsed result gets the real values back locally.
    AI_PSEUDONYMIZE_CONTACT_DETAILS: bool = True

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

    # Browser automation (V0.10). JobPilot fills application forms in a
    # Chromium it controls; the user can take over at any time. It never
    # submits an application.
    #
    # Visible Chromium is the default: the user sees and can use the very
    # window the assistant works in. Headless (servers, CI) is reported to the
    # user honestly: the live view is then a screenshot.
    AUTOMATION_BROWSER_HEADLESS: bool = False
    # Optional Chromium executable (otherwise Playwright's own).
    AUTOMATION_CHROMIUM_PATH: str | None = None
    # localhost / private networks are refused as an application URL unless
    # this is set (local development, tests); it protects the server from
    # being pointed at its own network.
    AUTOMATION_ALLOW_PRIVATE_HOSTS: bool = False
    AUTOMATION_NAVIGATION_TIMEOUT_SECONDS: float = 30.0
    # A browser that nobody has used for this long is closed.
    AUTOMATION_IDLE_CLOSE_MINUTES: int = 30
    # Live browsers per server process.
    AUTOMATION_MAX_BROWSERS: int = 3
    # Fields read from one page; more is "not a form JobPilot understands".
    AUTOMATION_MAX_FIELDS: int = 300

    # Review & submit (V0.11). Submission is a separate capability: it needs a
    # final review the user confirmed and a fresh, explicit authorization.
    # An authorization expires if it is not used within this time.
    SUBMISSION_AUTHORIZATION_TTL_SECONDS: int = 600
    # How long JobPilot watches the page for a confirmation after it pressed the
    # final button. Without one the result is SUBMISSION_UNKNOWN -- never a retry.
    SUBMISSION_RESULT_TIMEOUT_SECONDS: float = 20.0

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_development(self) -> bool:
        return self.APP_ENV == "development"

    @property
    def is_production(self) -> bool:
        return self.APP_ENV == "production"

    @property
    def hsts_enabled(self) -> bool:
        if self.SECURITY_HSTS_ENABLED is None:
            return self.is_production
        return self.SECURITY_HSTS_ENABLED

    @property
    def api_docs_enabled(self) -> bool:
        if self.API_DOCS_ENABLED is None:
            return not self.is_production
        return self.API_DOCS_ENABLED

def production_config_problems(config: "Settings") -> list[str]:
    """Development-only configuration that must never reach a production deployment."""
    problems: list[str] = []
    if (
        config.SECRET_KEY in _DEV_SECRET_KEYS
        or len(config.SECRET_KEY) < MIN_PRODUCTION_SECRET_KEY_LENGTH
    ):
        problems.append(
            f"SECRET_KEY must be a unique random value of at least "
            f"{MIN_PRODUCTION_SECRET_KEY_LENGTH} characters"
        )
    if _DEV_DATABASE_CREDENTIALS in config.DATABASE_URL:
        problems.append("DATABASE_URL uses the development credentials jobpilot/jobpilot")
    if not config.cors_origins:
        problems.append("BACKEND_CORS_ORIGINS must name the frontend origin(s)")
    if config.AUTOMATION_ALLOW_PRIVATE_HOSTS:
        problems.append("AUTOMATION_ALLOW_PRIVATE_HOSTS must be false")
    return problems


class InsecureConfigurationError(RuntimeError):
    """The configuration must not be used. The message names the problems only -- never
    a configured value (secrets, database passwords)."""


def validate_security(config: Settings) -> None:
    if any(origin == "*" for origin in config.cors_origins):
        raise InsecureConfigurationError(
            "BACKEND_CORS_ORIGINS must list explicit origins; '*' is not allowed."
        )
    if config.is_production:
        problems = production_config_problems(config)
        if problems:
            raise InsecureConfigurationError(
                "Refusing to start with APP_ENV=production: " + "; ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    config = Settings()
    validate_security(config)
    return config


settings = get_settings()
