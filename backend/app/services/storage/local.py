import shutil
from pathlib import Path
from typing import BinaryIO

from app.services.storage.base import FileStorage, StoredFile


class LocalFileStorage(FileStorage):
    """Development-grade storage backend that writes to the local filesystem.

    Intended for local development only. Production deployments should use
    an object-storage backed implementation (S3, GCS, Azure Blob, R2) that
    satisfies the same `FileStorage` interface.
    """

    def __init__(self, base_path: str) -> None:
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _resolve(self, reference: str) -> Path:
        return self._base_path / reference

    def save(self, *, key: str, content: BinaryIO) -> StoredFile:
        destination = self._resolve(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as f:
            shutil.copyfileobj(content, f)
        size = destination.stat().st_size
        return StoredFile(reference=key, size=size)

    def delete(self, *, reference: str) -> None:
        path = self._resolve(reference)
        path.unlink(missing_ok=True)

    def exists(self, *, reference: str) -> bool:
        return self._resolve(reference).exists()
