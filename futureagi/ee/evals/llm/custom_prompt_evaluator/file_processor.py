"""
File processor for eval inputs — handles images, audio, PDFs, and documents.

For image URLs: passed directly to vision-capable LLMs
For audio URLs: downloaded and transcribed (or passed to audio-capable models)
For PDF/doc URLs: text extracted and injected into the prompt
"""

import base64
import mimetypes
import structlog
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

logger = structlog.get_logger(__name__)


def classify_url(url: str) -> str:
    """Classify a URL by content type: image, audio, pdf, document, or unknown."""
    if not url or not isinstance(url, str):
        return "unknown"

    url_lower = url.lower().split("?")[0]  # Remove query params

    if any(url_lower.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp")):
        return "image"
    if any(url_lower.endswith(ext) for ext in (".mp3", ".wav", ".ogg", ".m4a", ".webm", ".flac", ".aac")):
        return "audio"
    if url_lower.endswith(".pdf"):
        return "pdf"
    if any(url_lower.endswith(ext) for ext in (".doc", ".docx", ".txt", ".rtf", ".md")):
        return "document"

    # Try content-type header
    try:
        mime, _ = mimetypes.guess_type(url)
        if mime:
            if mime.startswith("image/"):
                return "image"
            if mime.startswith("audio/"):
                return "audio"
            if mime == "application/pdf":
                return "pdf"
    except Exception:
        pass

    return "unknown"


def process_file_urls(file_urls: list[str]) -> dict:
    """
    Process a list of file URLs into LLM-compatible content.

    Returns:
        {
            "image_blocks": [{"type": "image_url", "image_url": {"url": ...}}],
            "text_context": "Extracted text from PDFs/docs/audio transcriptions",
        }
    """
    image_blocks = []
    text_parts = []

    for url in file_urls:
        if not isinstance(url, str) or not url.strip():
            continue

        url = url.strip()
        file_type = classify_url(url)

        if file_type == "image":
            image_blocks.append({
                "type": "image_url",
                "image_url": {"url": url},
            })

        elif file_type == "audio":
            # For audio: note in context that audio URL is available
            # Full transcription would require a speech-to-text service
            text_parts.append(f"[Audio file: {url}]")
            # Try to describe the audio reference
            text_parts.append(
                "Note: An audio file was provided. If the evaluation requires "
                "listening to the audio, the audio content should be transcribed "
                "or described before evaluation."
            )

        elif file_type == "pdf":
            # Try to extract text from PDF
            pdf_text = _extract_pdf_text(url)
            if pdf_text:
                text_parts.append(f"[Content from PDF: {url}]\n{pdf_text}")
            else:
                text_parts.append(f"[PDF file: {url} — could not extract text]")

        elif file_type == "document":
            doc_text = _fetch_text_content(url)
            if doc_text:
                text_parts.append(f"[Content from document: {url}]\n{doc_text}")
            else:
                text_parts.append(f"[Document file: {url}]")

        else:
            # Unknown type — include as reference
            text_parts.append(f"[File: {url}]")

    return {
        "image_blocks": image_blocks,
        "text_context": "\n\n".join(text_parts) if text_parts else "",
    }


def _extract_pdf_text(url: str, max_chars: int = 10000) -> Optional[str]:
    """Extract text from a PDF URL."""
    try:
        import io
        response = urlopen(url, timeout=15)
        pdf_bytes = response.read()

        # Try PyPDF2/pypdf
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(pdf_bytes))
            text = ""
            for page in reader.pages[:20]:  # Max 20 pages
                text += page.extract_text() + "\n"
                if len(text) > max_chars:
                    break
            return text[:max_chars] if text.strip() else None
        except ImportError:
            pass

        # Fallback: try pdfminer
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            text = pdfminer_extract(io.BytesIO(pdf_bytes), maxpages=20)
            return text[:max_chars] if text.strip() else None
        except ImportError:
            pass

        logger.warning("pdf_extraction_no_library", url=url)
        return None

    except Exception as e:
        logger.warning("pdf_extraction_failed", url=url, error=str(e))
        return None


def _fetch_text_content(url: str, max_chars: int = 10000) -> Optional[str]:
    """Fetch plain text content from a URL."""
    try:
        response = urlopen(url, timeout=15)
        content = response.read().decode("utf-8", errors="ignore")
        return content[:max_chars] if content.strip() else None
    except Exception as e:
        logger.warning("text_fetch_failed", url=url, error=str(e))
        return None
