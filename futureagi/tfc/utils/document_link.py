"""Helpers for dataset document-cell Link writes.

Kept independent of ``tfc.utils.storage`` so link byte inspection can be
tested without pulling image/audio/MinIO dependencies.  This is deliberately
stricter than the general document upload path: an interactive web Link must
prove its type from its downloaded bytes, rather than merely claiming a suffix
or Content-Type.
"""

import csv
import io
import zipfile
from urllib.parse import unquote, urlparse

DOCUMENT_NOT_A_WEB_ADDRESS = "The value is not a web address."
DOCUMENT_ADDRESS_UNREACHABLE = "The address cannot be reached."
DOCUMENT_ADDRESS_NOT_A_DOCUMENT = "The address is not a document."
DOCUMENT_ADDRESS_TOO_LARGE = "The document is larger than 100 MiB."


class DocumentLinkValidationError(ValueError):
    """A safe, user-presentable rejection of a document Link candidate."""


class DocumentLinkFetchError(ValueError):
    """A redacted network or HTTP failure while resolving a document Link."""


class DocumentLinkTooLargeError(DocumentLinkValidationError):
    """The download exceeded the document Link size ceiling."""


_GENERIC_CONTENT_TYPES = frozenset(
    {
        "",
        "application/octet-stream",
        "binary/octet-stream",
        # OOXML is a ZIP container; this does not conflict with a type proven
        # by its internal Word/Excel/PowerPoint members.
        "application/zip",
        "application/x-zip-compressed",
    }
)
_DISALLOWED_LINK_CONTENT_TYPES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/xml",
        "text/xml",
        "application/vnd.oasis.opendocument.text",
    }
)
_OOXML_TYPES = {
    "word/document.xml": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    "xl/workbook.xml": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    ),
    "ppt/presentation.xml": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
}
_OLE_STREAM_TYPES = {
    "WordDocument": "application/msword",
    "Workbook": "application/vnd.ms-excel",
    "PowerPoint Document": "application/vnd.ms-powerpoint",
}
_RTF_CONTENT_TYPES = frozenset({"text/rtf", "application/rtf"})


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


def document_link_display_name(value: str) -> str:
    """Return a safe display name without retaining signed query parameters."""
    parsed = urlparse(value)
    filename = unquote((parsed.path or "").rsplit("/", 1)[-1]).strip()
    return filename[:400] if filename else "document"


def _main_content_type(value: str | None) -> str:
    return (value or "").split(";", 1)[0].strip().lower()


def _validated_text(content: bytes) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DocumentLinkValidationError("The address is not a document.") from exc
    if not text.strip() or "\x00" in text:
        raise DocumentLinkValidationError("The address is not a document.")
    # Plain text may legitimately include markup examples.  Only reject binary
    # control data; do not apply keyword or markup blacklists here.
    controls = sum(
        ord(char) < 32 and char not in "\t\n\r" for char in text
    )
    if controls > max(1, len(text) // 100):
        raise DocumentLinkValidationError("The address is not a document.")
    return text


def _ole_stream_names(content: bytes) -> set[str]:
    """Read CFB directory stream names without an additional dependency.

    DOC, XLS and PPT use the same Compound File Binary container.  Looking at
    the container magic alone is ambiguous, so parse its FAT/DIFAT and
    directory entries and require one of the application-specific streams.
    """
    free_sector = 0xFFFFFFFF
    end_of_chain = 0xFFFFFFFE
    if len(content) < 512 or content[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise DocumentLinkValidationError("The address is not a document.")
    if content[28:30] != b"\xfe\xff":
        raise DocumentLinkValidationError("The address is not a document.")

    sector_shift = int.from_bytes(content[30:32], "little")
    if sector_shift not in (9, 12):
        raise DocumentLinkValidationError("The address is not a document.")
    sector_size = 1 << sector_shift
    first_directory_sector = int.from_bytes(content[48:52], "little")
    number_of_fat_sectors = int.from_bytes(content[44:48], "little")
    first_difat_sector = int.from_bytes(content[68:72], "little")
    number_of_difat_sectors = int.from_bytes(content[72:76], "little")

    def sector(sector_id: int) -> bytes:
        start = (sector_id + 1) * sector_size
        end = start + sector_size
        if sector_id >= free_sector - 3 or end > len(content):
            raise DocumentLinkValidationError("The address is not a document.")
        return content[start:end]

    fat_sector_ids = [
        int.from_bytes(content[offset : offset + 4], "little")
        for offset in range(76, 512, 4)
    ]
    fat_sector_ids = [
        sector_id for sector_id in fat_sector_ids if sector_id != free_sector
    ]
    difat_sector = first_difat_sector
    for _ in range(number_of_difat_sectors):
        difat = sector(difat_sector)
        fat_sector_ids.extend(
            sector_id
            for sector_id in (
                int.from_bytes(difat[offset : offset + 4], "little")
                for offset in range(0, sector_size - 4, 4)
            )
            if sector_id != free_sector
        )
        difat_sector = int.from_bytes(difat[-4:], "little")

    if number_of_fat_sectors == 0 or len(fat_sector_ids) < number_of_fat_sectors:
        raise DocumentLinkValidationError("The address is not a document.")
    fat = b"".join(
        sector(sector_id) for sector_id in fat_sector_ids[:number_of_fat_sectors]
    )

    def next_sector(sector_id: int) -> int:
        offset = sector_id * 4
        if offset + 4 > len(fat):
            raise DocumentLinkValidationError("The address is not a document.")
        return int.from_bytes(fat[offset : offset + 4], "little")

    directory = bytearray()
    seen = set()
    directory_sector = first_directory_sector
    max_sectors = (len(content) // sector_size) + 1
    while directory_sector != end_of_chain:
        if directory_sector in seen or len(seen) >= max_sectors:
            raise DocumentLinkValidationError("The address is not a document.")
        seen.add(directory_sector)
        directory.extend(sector(directory_sector))
        directory_sector = next_sector(directory_sector)

    names = set()
    for offset in range(0, len(directory), 128):
        entry = directory[offset : offset + 128]
        if len(entry) < 128 or entry[66] != 2:  # 2 == user stream
            continue
        name_length = int.from_bytes(entry[64:66], "little")
        if name_length < 2 or name_length > 64 or name_length % 2:
            continue
        try:
            names.add(entry[: name_length - 2].decode("utf-16-le"))
        except UnicodeDecodeError:
            continue
    return names


def _detect_binary_document_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-") and b"%%EOF" in content[-1024:]:
        return "application/pdf"
    if content.startswith(b"{\\rtf"):
        return "text/rtf"
    if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        names = _ole_stream_names(content)
        matching_types = {
            content_type
            for stream_name, content_type in _OLE_STREAM_TYPES.items()
            if stream_name in names
        }
        if len(matching_types) != 1:
            raise DocumentLinkValidationError("The address is not a document.")
        return matching_types.pop()
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
                if (
                    archive.read("mimetype")
                    == b"application/vnd.oasis.opendocument.text"
                ):
                    raise DocumentLinkValidationError("The address is not a document.")
        except KeyError:
            pass
        except zipfile.BadZipFile as exc:
            raise DocumentLinkValidationError("The address is not a document.") from exc

        matching_types = {
            content_type
            for member, content_type in _OOXML_TYPES.items()
            if member in names
        }
        if "[Content_Types].xml" not in names or len(matching_types) != 1:
            raise DocumentLinkValidationError("The address is not a document.")
        return matching_types.pop()
    return None


def _content_types_are_compatible(detected: str, declared: str) -> bool:
    if declared in _GENERIC_CONTENT_TYPES:
        return True
    if detected == "text/rtf":
        return declared in _RTF_CONTENT_TYPES
    return detected == declared


def inspect_document_link_bytes(
    content: bytes, declared_content_type: str | None
) -> str:
    """Return the verified Link MIME type or raise a safe validation error.

    MIME headers refine the classification but never establish a binary type by
    themselves.  Plain text is byte-validated as UTF-8, which intentionally
    permits documentation containing markup examples or words such as
    ``login``.  HTML, XML and ODT attachments remain disallowed Link types.
    """
    if not content:
        raise DocumentLinkValidationError("The address is not a document.")
    declared = _main_content_type(declared_content_type)
    if declared in _DISALLOWED_LINK_CONTENT_TYPES:
        raise DocumentLinkValidationError("The address is not a document.")

    detected = _detect_binary_document_type(content)
    if detected:
        if not _content_types_are_compatible(detected, declared):
            raise DocumentLinkValidationError("The address is not a document.")
        return detected

    _validated_text(content)
    if declared == "text/csv":
        try:
            next(csv.reader(io.StringIO(content.decode("utf-8"))))
        except (StopIteration, csv.Error, UnicodeDecodeError) as exc:
            raise DocumentLinkValidationError("The address is not a document.") from exc
        return "text/csv"
    if declared in _GENERIC_CONTENT_TYPES or declared == "text/plain":
        return "text/plain"
    raise DocumentLinkValidationError("The address is not a document.")


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
    if isinstance(exc, DocumentLinkTooLargeError):
        return DOCUMENT_ADDRESS_TOO_LARGE
    if isinstance(exc, DocumentLinkValidationError):
        return DOCUMENT_ADDRESS_NOT_A_DOCUMENT
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
