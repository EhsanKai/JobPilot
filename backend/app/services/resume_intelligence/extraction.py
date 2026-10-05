"""Stage 1 of the pipeline: turn an uploaded file into normalized plain text.

Extraction is local and deterministic (pypdf / python-docx); no external
service is involved. Scanned PDFs are detected rather than silently
producing an empty profile -- OCR is a future capability.
"""

import asyncio
import io
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)

PDF_MIME_TYPE = "application/pdf"
DOCX_MIME_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# A PDF yielding fewer meaningful (alphanumeric) characters than this --
# in total, or on average per page -- is treated as image-only / scanned.
_MIN_TOTAL_CHARS = 40
_MIN_CHARS_PER_PAGE = 20


class ExtractionError(Exception):
    """Base for extraction failures. `code`/`user_message` are user-safe."""

    code = "EXTRACTION_FAILED"
    user_message = (
        "We couldn't read the text in this file. It may be corrupted or password-protected."
    )


class OcrRequiredError(ExtractionError):
    code = "OCR_REQUIRED"
    user_message = (
        "This PDF does not contain extractable text. "
        "OCR support will be added in a future version."
    )


class EmptyDocumentError(ExtractionError):
    code = "EMPTY_DOCUMENT"
    user_message = "This document doesn't contain any text to analyze."


class UnsupportedFormatError(ExtractionError):
    code = "UNSUPPORTED_FORMAT"
    user_message = "Only PDF and DOCX resumes can be analyzed."


@dataclass(frozen=True)
class ExtractedText:
    text: str
    method: str
    page_count: int | None = None

    @property
    def char_count(self) -> int:
        return len(self.text)


_BULLETS = re.compile(r"^[ \t]*[•●▪◦‣∙·■□➢➤►\uf0b7\uf0a7][ \t]*", re.MULTILINE)
_INLINE_SPACE = re.compile(r"[ \t  -​  　]+")
_MANY_BLANK_LINES = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """Normalize extracted text without changing its meaning.

    Unicode NFKC (ligatures like "ﬁ" -> "fi"), unified line endings, bullets
    turned into "- ", collapsed runs of spaces, at most one blank line.
    """
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("­", "")  # soft hyphen
    text = _BULLETS.sub("- ", text)
    text = _INLINE_SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MANY_BLANK_LINES.sub("\n\n", text)
    return text.strip()


def _meaningful_chars(text: str) -> int:
    return sum(1 for ch in text if ch.isalnum())


class ResumeTextExtractor(ABC):
    """Contract for turning one file format into `ExtractedText`."""

    method: str = "unknown"

    @abstractmethod
    async def extract(self, file: bytes) -> ExtractedText:
        raise NotImplementedError


class PdfTextExtractor(ResumeTextExtractor):
    method = "pypdf"

    async def extract(self, file: bytes) -> ExtractedText:
        # CPU-bound parsing runs off the event loop.
        return await asyncio.to_thread(self._extract_sync, file)

    def _extract_sync(self, file: bytes) -> ExtractedText:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError

        try:
            reader = PdfReader(io.BytesIO(file))
            if reader.is_encrypted:
                # Many "encrypted" PDFs only restrict editing and open with an
                # empty password; anything else can't be read.
                try:
                    if not reader.decrypt(""):
                        raise ExtractionError("encrypted PDF")
                except Exception as exc:
                    raise ExtractionError("encrypted PDF") from exc
            pages = [page.extract_text() or "" for page in reader.pages]
        except ExtractionError:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError, OSError) as exc:
            raise ExtractionError(f"unreadable PDF ({type(exc).__name__})") from None

        page_count = len(pages)
        text = normalize_text("\n\n".join(pages))
        meaningful = _meaningful_chars(text)
        if page_count == 0:
            raise EmptyDocumentError("PDF has no pages")
        if meaningful < max(_MIN_TOTAL_CHARS, _MIN_CHARS_PER_PAGE * page_count):
            raise OcrRequiredError(f"{meaningful} text chars across {page_count} page(s)")
        return ExtractedText(text=text, method=self.method, page_count=page_count)


class DocxTextExtractor(ResumeTextExtractor):
    method = "python-docx"

    async def extract(self, file: bytes) -> ExtractedText:
        return await asyncio.to_thread(self._extract_sync, file)

    def _extract_sync(self, file: bytes) -> ExtractedText:
        import zipfile

        from docx import Document
        from docx.opc.exceptions import PackageNotFoundError

        try:
            document = Document(io.BytesIO(file))
        except (PackageNotFoundError, zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise ExtractionError(f"unreadable DOCX ({type(exc).__name__})") from None

        lines: list[str] = []
        for section in document.sections:
            for paragraph in section.header.paragraphs:
                lines.append(paragraph.text)

        # Walk the body in document order so tables (often used for skills or
        # two-column layouts) stay next to their surrounding paragraphs.
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        for block in document.iter_inner_content():
            if isinstance(block, Paragraph):
                lines.append(block.text)
            elif isinstance(block, Table):
                for row in block.rows:
                    cells: list[str] = []
                    for cell in row.cells:
                        cell_text = cell.text.strip()
                        # Merged cells repeat; keep each once per row.
                        if cell_text and cell_text not in cells:
                            cells.append(cell_text)
                    if cells:
                        lines.append(" | ".join(cells))

        text = normalize_text("\n".join(lines))
        if _meaningful_chars(text) == 0:
            raise EmptyDocumentError("DOCX contains no text")
        return ExtractedText(text=text, method=self.method, page_count=None)


_EXTRACTORS: dict[str, type[ResumeTextExtractor]] = {
    PDF_MIME_TYPE: PdfTextExtractor,
    DOCX_MIME_TYPE: DocxTextExtractor,
}


def get_extractor(mime_type: str) -> ResumeTextExtractor:
    extractor_cls = _EXTRACTORS.get(mime_type)
    if extractor_cls is None:
        raise UnsupportedFormatError(f"no extractor for {mime_type}")
    return extractor_cls()
