"""Bring the database schema to the current Alembic head -- the container startup step.

    python -m app.db.migrate            wait for the database, migrate, verify
    python -m app.db.migrate --check    wait, then only verify (exit 1 if not at head)

The backend container runs this before it starts the API (see backend/docker-entrypoint.sh),
so a fresh database is always initialized and an existing one is upgraded. It never
swallows a failure: if the database stays unreachable, a migration raises or the schema is
not at head afterwards, it logs why and exits non-zero, and the container does not start
the API on a half-initialized schema.

The wait is a bounded readiness loop on a real connection (with backoff), not a fixed sleep;
in Docker Compose, PostgreSQL's own health check already gates the backend, so it normally
connects on the first attempt.

Concurrent starts (several backend replicas) are serialized by a PostgreSQL advisory lock,
so two processes never run the same migration at once.
"""

import argparse
import logging
import sys
import time
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.config import settings
from app.core.logging import configure_logging, get_logger

logger = get_logger("jobpilot.migrate")

BACKEND_ROOT = Path(__file__).resolve().parents[2]
# Arbitrary, fixed: identifies "JobPilot schema migration" among advisory locks.
_MIGRATION_LOCK_ID = 7_301_120_013


class MigrationError(RuntimeError):
    """The schema could not be brought to (or verified at) the Alembic head."""


def alembic_config() -> Config:
    # No ini file: env.py would otherwise re-run logging.fileConfig and replace the app's
    # logging configuration. env.py reads the database URL from the app settings.
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def head_revisions(config: Config | None = None) -> set[str]:
    return set(ScriptDirectory.from_config(config or alembic_config()).get_heads())


def current_revisions(connection: Connection) -> set[str]:
    return set(MigrationContext.configure(connection).get_current_heads())


@lru_cache(maxsize=1)
def _code_heads() -> frozenset[str]:
    # The migration scripts ship with the code: read them once per process.
    return frozenset(head_revisions())


def schema_is_current(engine: Engine) -> bool:
    """True when the database is reachable and migrated to the code's Alembic head."""
    with engine.connect() as connection:
        return current_revisions(connection) == _code_heads()


def wait_for_database(engine: Engine, *, timeout_seconds: float) -> None:
    """Retry a real connection until it succeeds or `timeout_seconds` passes."""
    deadline = time.monotonic() + timeout_seconds
    delay = 0.5
    attempt = 0
    while True:
        attempt += 1
        try:
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            if attempt > 1:
                logger.info("event=db_ready attempts=%d", attempt)
            return
        except OperationalError as exc:
            if time.monotonic() + delay > deadline:
                # The driver message names host/port/database, never the password.
                raise MigrationError(
                    f"database not reachable after {attempt} attempts "
                    f"({timeout_seconds:.0f}s): {exc.orig}"
                ) from exc
            logger.info("event=db_waiting attempt=%d retry_in=%.1fs", attempt, delay)
            time.sleep(delay)
            delay = min(delay * 2, 5.0)


def migrate(engine: Engine, *, config: Config | None = None) -> str:
    """Upgrade to head under an advisory lock and verify. Returns the head revision."""
    config = config or alembic_config()
    heads = head_revisions(config)
    if len(heads) != 1:
        raise MigrationError(f"expected exactly one Alembic head, found {sorted(heads)}")
    with engine.connect() as connection:
        connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
        connection.commit()
        try:
            before = current_revisions(connection)
            logger.info(
                "event=migration_start current=%s head=%s",
                ",".join(sorted(before)) or "<empty database>",
                ",".join(sorted(heads)),
            )
            command.upgrade(config, "head")
            # A fresh read: the upgrade ran on its own connection.
            connection.rollback()
            after = current_revisions(connection)
        finally:
            connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _MIGRATION_LOCK_ID})
            connection.commit()
    if after != heads:
        raise MigrationError(
            f"schema is at {sorted(after)} after the upgrade, expected {sorted(heads)}"
        )
    (head,) = heads
    logger.info("event=migration_done revision=%s", head)
    return head


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true", help="only verify that the schema is at head"
    )
    parser.add_argument(
        "--wait-seconds",
        type=float,
        default=settings.DB_STARTUP_TIMEOUT_SECONDS,
        help="how long to wait for the database to accept connections",
    )
    args = parser.parse_args(argv)

    configure_logging()
    # Alembic reports each applied revision at INFO; keep that visible at startup.
    logging.getLogger("alembic").setLevel(logging.INFO)
    engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    try:
        wait_for_database(engine, timeout_seconds=args.wait_seconds)
        if args.check:
            if not schema_is_current(engine):
                logger.error("event=schema_not_current")
                return 1
            logger.info("event=schema_current")
            return 0
        migrate(engine)
        return 0
    except MigrationError as exc:
        logger.error("event=migration_failed reason=%s", exc)
        return 1
    except Exception:
        # A failing migration script: show the traceback, then fail the startup.
        logger.exception("event=migration_failed reason=unexpected_error")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
