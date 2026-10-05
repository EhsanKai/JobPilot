import pytest

from app.services.resume_intelligence.extraction import (
    DOCX_MIME_TYPE,
    PDF_MIME_TYPE,
    DocxTextExtractor,
    EmptyDocumentError,
    ExtractionError,
    OcrRequiredError,
    PdfTextExtractor,
    UnsupportedFormatError,
    get_extractor,
    normalize_text,
)
from tests.resume_helpers import make_blank_pdf, make_docx, make_text_pdf, sample_resume_text


async def test_pdf_text_extraction_returns_normalized_text() -> None:
    extracted = await PdfTextExtractor().extract(make_text_pdf(sample_resume_text()))

    assert extracted.method == "pypdf"
    assert extracted.page_count == 1
    assert "Alex Example" in extracted.text
    assert "Example Energy GmbH, Berlin" in extracted.text
    # Non-ASCII characters survive extraction.
    assert "Technische Universität Example" in extracted.text
    assert extracted.char_count == len(extracted.text)


async def test_docx_text_extraction_includes_paragraphs_tables_and_header() -> None:
    content = make_docx(
        "Alex Example\nSAP IS-U Developer",
        table_rows=[["Skills", "ABAP, SAP IS-U"], ["Languages", "German - native"]],
        header="Curriculum Vitae",
    )

    extracted = await DocxTextExtractor().extract(content)

    assert extracted.method == "python-docx"
    assert "Curriculum Vitae" in extracted.text
    assert "Alex Example" in extracted.text
    assert "Skills | ABAP, SAP IS-U" in extracted.text
    assert "Languages | German - native" in extracted.text


async def test_scanned_pdf_without_text_requires_ocr() -> None:
    with pytest.raises(OcrRequiredError) as exc_info:
        await PdfTextExtractor().extract(make_blank_pdf(pages=2))

    assert exc_info.value.code == "OCR_REQUIRED"
    assert "OCR support will be added in a future version" in exc_info.value.user_message


async def test_pdf_with_almost_no_text_requires_ocr() -> None:
    # e.g. a scanned resume whose only text layer is a page number.
    with pytest.raises(OcrRequiredError):
        await PdfTextExtractor().extract(make_text_pdf("1"))


async def test_corrupted_pdf_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await PdfTextExtractor().extract(b"%PDF-1.4 this is not really a pdf")
    assert exc_info.value.code in {"EXTRACTION_FAILED", "OCR_REQUIRED", "EMPTY_DOCUMENT"}


async def test_garbage_pdf_bytes_raise_extraction_failed() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await PdfTextExtractor().extract(b"definitely not a pdf")
    assert exc_info.value.code == "EXTRACTION_FAILED"


async def test_corrupted_docx_raises_extraction_error() -> None:
    with pytest.raises(ExtractionError) as exc_info:
        await DocxTextExtractor().extract(b"not a zip archive")
    assert exc_info.value.code == "EXTRACTION_FAILED"


async def test_empty_docx_raises_empty_document() -> None:
    with pytest.raises(EmptyDocumentError):
        await DocxTextExtractor().extract(make_docx(""))


def test_get_extractor_selects_by_mime_type() -> None:
    assert isinstance(get_extractor(PDF_MIME_TYPE), PdfTextExtractor)
    assert isinstance(get_extractor(DOCX_MIME_TYPE), DocxTextExtractor)
    with pytest.raises(UnsupportedFormatError):
        get_extractor("text/plain")


def test_normalize_text_preserves_meaning() -> None:
    raw = "Proﬁle\r\n\r\n\r\n\r\n•  Developed   ABAP reports  \n▪ Second\x00 item"
    assert normalize_text(raw) == "Profile\n\n- Developed ABAP reports\n- Second item"
