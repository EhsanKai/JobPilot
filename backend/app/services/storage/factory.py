from functools import lru_cache

from app.core.config import settings
from app.services.storage.base import FileStorage
from app.services.storage.local import LocalFileStorage


@lru_cache
def get_file_storage() -> FileStorage:
    """Resolve the configured FileStorage backend.

    Only `local` is implemented in V0. `STORAGE_PROVIDER` exists so future
    versions can add s3 / gcs / azure_blob / r2 without changing callers.
    """
    if settings.STORAGE_PROVIDER == "local":
        return LocalFileStorage(settings.STORAGE_PATH)
    raise NotImplementedError(
        f"Storage provider '{settings.STORAGE_PROVIDER}' is not implemented in V0."
    )
