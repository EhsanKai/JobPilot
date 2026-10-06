import re
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, event, func, inspect
from sqlalchemy.orm import Mapped, Mapper, mapped_column


class ImmutableRecordError(Exception):
    """Raised when code tries to change a column of an immutable record."""


class CreatedAtMixin:
    """Adds a database-managed `created_at` column, for write-once records
    that have no meaningful `updated_at`."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ImmutableMixin:
    """Marks a model as write-once (V0.5).

    Any ORM flush that would change a column not listed in
    `__mutable_columns__` (pointer/state fields such as `is_current`) raises
    `ImmutableRecordError`. Deletes are allowed (they cascade from users and
    jobs). This guards application code; it does not stop raw SQL.
    """

    __mutable_columns__: ClassVar[frozenset[str]] = frozenset()


@event.listens_for(ImmutableMixin, "before_update", propagate=True)
def _reject_immutable_changes(mapper: Mapper[Any], connection: Any, target: Any) -> None:
    state = inspect(target)
    changed = {
        attr.key for attr in mapper.column_attrs if state.attrs[attr.key].history.has_changes()
    }
    forbidden = changed - target.__mutable_columns__
    if forbidden:
        raise ImmutableRecordError(
            f"{type(target).__name__} is immutable; cannot change {sorted(forbidden)}"
        )


_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def require_sha256_hex(key: str, value: str) -> str:
    """Validate a hash column of a V0.5 record: a lowercase SHA-256 hex
    digest. These hashes are identity/deduplication keys, so a malformed one
    is rejected rather than stored."""
    if not isinstance(value, str) or not _SHA256_HEX.match(value):
        raise ValueError(f"{key} must be a lowercase SHA-256 hex digest")
    return value


class TimestampMixin:
    """Adds created_at / updated_at columns managed by the database."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
