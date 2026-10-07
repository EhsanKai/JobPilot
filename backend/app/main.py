from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.http_security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.logging import configure_logging, get_logger
from app.db.migrate import schema_is_current
from app.db.session import engine

configure_logging()
logger = get_logger(__name__)

_docs = settings.api_docs_enabled
app = FastAPI(
    title="JobPilot API",
    version="0.13.0",
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

# The frontend authenticates with a bearer token in the Authorization header, never with
# cookies, so CORS does not need (and does not allow) credentials. Origins are explicit
# (config validation rejects "*").
# Middleware order (outermost first): security headers -> CORS -> body size limit -> app,
# so a 413 still carries CORS headers and the browser can show it.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_MB * 1024 * 1024)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.add_middleware(SecurityHeadersMiddleware, hsts=settings.hsts_enabled)

register_exception_handlers(app)
app.include_router(api_router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Liveness: the process serves HTTP. Says nothing about the database."""
    return {"status": "ok"}


@app.get("/health/ready", tags=["health"], response_model=None)
def readiness_check() -> dict[str, str] | JSONResponse:
    """Readiness: the database answers and its schema is at the code's Alembic head.

    Used by the container health check, so Docker Compose only reports the backend healthy
    (and starts the frontend) once requests can actually succeed. Reports no internals.
    """
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        current = schema_is_current(engine)
    except SQLAlchemyError:
        logger.warning("event=readiness_database_unavailable")
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "database": "unreachable"}
        )
    if not current:
        logger.warning("event=readiness_schema_not_current")
        return JSONResponse(
            status_code=503, content={"status": "unavailable", "database": "not_migrated"}
        )
    return {"status": "ok", "database": "ok"}
