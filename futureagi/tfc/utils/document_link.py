"""Helpers for dataset document-cell Link writes.

Kept independent of ``tfc.utils.storage`` so URL classification can be tested
without pulling image/audio/MinIO dependencies.
"""

from urllib.parse import urlparse

DOCUMENT_NOT_A_WEB_ADDRESS = "The value is not a web address."
DOCUMENT_ADDRESS_UNREACHABLE = "The address cannot be reached."
DOCUMENT_ADDRESS_NOT_A_DOCUMENT = "The address is not a document."


def is_http_web_address(value) -> bool:
    """True for http(s) URLs with a host. Data URIs are not web addresses."""
    if not isinstance(value, str) or not value.strip():
        return False
    value = value.strip()
    if value.startswith("data:"):
        return False
    try:
        parsed = urlparse(value)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def resolve_document_cell_input(original_value, converted_value):
    """Classify a document-cell write before mutating storage.

    Returns one of:
        ("clear", None) — intentional empty write
        ("reject", message) — refuse without changing the existing cell
        ("store", converted_value) — proceed to upload
    """
    converted_empty = converted_value is None or (
        isinstance(converted_value, str) and converted_value.strip() == ""
    )
    original_empty = original_value is None or (
        isinstance(original_value, str) and str(original_value).strip() == ""
    )

    if converted_empty:
        if original_empty:
            return "clear", None
        return "reject", DOCUMENT_NOT_A_WEB_ADDRESS

    if isinstance(converted_value, str) and not converted_value.startswith("data:"):
        if not is_http_web_address(converted_value):
            return "reject", DOCUMENT_NOT_A_WEB_ADDRESS

    return "store", converted_value


def document_link_failure_message(exc: BaseException) -> str:
    """Map a document-link fetch/upload failure to a user-facing reason."""
    msg = str(exc).lower()
    if (
        "invalid document data" in msg
        or "file type is not supported" in msg
        or "downloaded file is empty" in msg
    ):
        return DOCUMENT_ADDRESS_NOT_A_DOCUMENT
    if "url is not valid" in msg or "not a web address" in msg:
        return DOCUMENT_NOT_A_WEB_ADDRESS
    return DOCUMENT_ADDRESS_UNREACHABLE
