from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO


@dataclass(frozen=True)
class StoredFile:
    """Reference to a file persisted by a FileStorage backend."""

    reference: str
    size: int


class FileStorage(ABC):
    """Storage abstraction so the application layer never depends on a
    specific backend (local disk, S3, GCS, Azure Blob, R2, ...).

    V0 only ships `LocalFileStorage`. Future backends must implement this
    same interface so services/routers never need to change.
    """

    @abstractmethod
    def save(self, *, key: str, content: BinaryIO) -> StoredFile:
        """Persist `content` under `key` and return its storage reference."""

    @abstractmethod
    def read(self, *, reference: str) -> bytes:
        """Return the stored bytes for `reference`.

        Raises `FileNotFoundError` if the file no longer exists. Reading never
        modifies the stored original (V0.2 relies on this to re-process
        resumes later).
        """

    @abstractmethod
    def delete(self, *, reference: str) -> None:
        """Remove a previously stored file. No-op if it does not exist."""

    @abstractmethod
    def exists(self, *, reference: str) -> bool:
        """Whether a file still exists for the given reference."""

    @abstractmethod
    def delete_owner(self, *, owner_id: str) -> int:
        """Remove every file stored for one owner (keys `{owner_id}/...`, V0.13 account
        deletion). Returns how many files were removed; no-op if there are none."""
