from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

# hide_parameters: in production, a failing statement's error (and so the logged traceback)
# names the SQL but never the bound values -- CV text, answers, e-mail addresses.
engine = create_engine(
    settings.DATABASE_URL, pool_pre_ping=True, hide_parameters=settings.is_production
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session_factory() -> sessionmaker[Session]:
    """Session factory for work that outlives a request (background tasks).

    A FastAPI dependency so tests can point background work at their own
    database.
    """
    return SessionLocal
