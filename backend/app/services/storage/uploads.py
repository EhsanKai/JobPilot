"""Upload hygiene shared by every file upload (V0.13).

- `read_bounded` reads at most `limit + 1` bytes, so an oversized upload is detected
  without holding all of it (the request body itself is capped by BodySizeLimitMiddleware).
- `safe_file_name` keeps a user's file name for display only: no directory parts, no
  control characters, bounded length. Storage keys never contain it (they are
  `{user_id}/{uuid}.{ext}`), so a name can never choose where a file is written.
- `sniff_document` decides what a resume file *is* from its bytes, not from the
  browser-supplied MIME type or the extension.
"""

import io
import unicodedata
import zipfile

from fastapi import UploadFile

_MAX_NAME_LENGTH = 255
# A real DOCX is a ZIP of a few dozen parts; anything far beyond these is not a resume
# (or is a decompression bomb).
_MAX_DOCX_ENTRIES = 2_000
_MAX_DOCX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def read_bounded(upload: UploadFile, limit: int) -> bytes:
    """The upload's bytes; at most `limit + 1` of them, so `len(result) > limit` means
    "too large" without reading the rest."""
    return await upload.read(limit + 1)


def safe_file_name(name: str | None, fallback: str) -> str:
    """A display name derived from the uploaded file name."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(
        ch for ch in unicodedata.normalize("NFC", base) if unicodedata.category(ch)[0] != "C"
    ).strip(" .")
    if not cleaned:
        return fallback
    if len(cleaned) > _MAX_NAME_LENGTH:
        stem, dot, extension = cleaned.rpartition(".")
        if dot and 0 < len(extension) <= 10:
            cleaned = stem[: _MAX_NAME_LENGTH - len(extension) - 1] + "." + extension
        else:
            cleaned = cleaned[:_MAX_NAME_LENGTH]
    return cleaned


def _is_docx(content: bytes) -> bool:
    if not content.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_DOCX_ENTRIES:
                return False
            if sum(entry.file_size for entry in entries) > _MAX_DOCX_UNCOMPRESSED_BYTES:
                return False
            names = {entry.filename for entry in entries}
    except (zipfile.BadZipFile, ValueError, OSError):
        return False
    return "[Content_Types].xml" in names and "word/document.xml" in names


def sniff_document(content: bytes) -> str | None:
    """PDF or DOCX MIME type from the file's own bytes, or None for anything else."""
    if content.startswith(b"%PDF-"):
        return PDF
    if _is_docx(content):
        return DOCX
    return None
