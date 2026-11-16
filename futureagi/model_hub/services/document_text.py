"""Bounded text extraction shared by KB indexing and Environment imports."""

from __future__ import annotations

import io
from pathlib import PurePath

from striprtf.striprtf import rtf_to_text

MAX_DOCUMENT_BYTES = 25 * 1024 * 1024
MAX_DOCUMENT_TEXT_CHARS = 10 * 1024 * 1024
SUPPORTED_DOCUMENT_SUFFIXES = frozenset({".pdf", ".docx", ".rtf", ".txt"})


class DocumentTextError(ValueError):
    pass


def normalize_document_text(text: str) -> str:
    normalized = " ".join(text.replace("\x00", "").split()).strip()
    if not normalized:
        raise DocumentTextError("Document contains no extractable text.")
    if len(normalized) > MAX_DOCUMENT_TEXT_CHARS:
        raise DocumentTextError("Extracted document exceeds the 10 MiB text limit.")
    return normalized


def extract_pdf_reader_text(reader) -> str:
    return normalize_document_text(
        "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
    )


def extract_document_text(content: bytes, filename: str) -> str:
    """Extract normalized text without filesystem or indexing side effects."""
    if not isinstance(content, bytes) or len(content) > MAX_DOCUMENT_BYTES:
        raise DocumentTextError("Document exceeds the 25 MiB import limit.")
    suffix = PurePath(filename).suffix.lower()
    if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
        raise DocumentTextError("Document type is not supported.")
    try:
        if suffix == ".pdf":
            from pypdf import PdfReader

            return extract_pdf_reader_text(PdfReader(io.BytesIO(content)))
        elif suffix == ".docx":
            import docx

            text = "\n\n".join(
                paragraph.text
                for paragraph in docx.Document(io.BytesIO(content)).paragraphs
            )
        elif suffix == ".rtf":
            text = rtf_to_text(content.decode("utf-8"))
        else:
            text = content.decode("utf-8")
    except ImportError:
        raise
    except Exception as exc:
        raise DocumentTextError("Document text could not be extracted.") from exc
    return normalize_document_text(text)
