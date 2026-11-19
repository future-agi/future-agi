"""Bound ZIP metadata before ZipFile eagerly allocates its ZipInfo inventory."""

from __future__ import annotations

import io
import struct
import zipfile
from typing import Any, Protocol, cast

from model_hub.services.skills_hub import limits


class _ZipLayout(Protocol):
    """The CPython layout API used below, absent from public zipfile stubs."""

    _ECD_DISK_NUMBER: int
    _ECD_DISK_START: int
    _ECD_ENTRIES_TOTAL: int
    _ECD_ENTRIES_THIS_DISK: int
    _ECD_SIZE: int
    _ECD_LOCATION: int
    _ECD_SIGNATURE: int
    _ECD_OFFSET: int
    stringEndArchive64: bytes
    sizeEndCentDir64: int
    sizeEndCentDir64Locator: int
    sizeCentralDir: int
    stringCentralDir: bytes

    def _EndRecData(self, stream: io.BytesIO) -> list[Any] | None: ...


_zip_layout = cast(_ZipLayout, zipfile)


class ZipDirectoryTooLarge(ValueError):
    """The directory exceeds its entry or byte budget, even without file data."""


def preflight_zip_directory(raw: bytes, *, max_entries: int) -> int:
    """Check declared sizes AND count actual records without creating ZipInfo.

    Use the same bounded EOCD/ZIP64 reader and offset calculation as ZipFile so
    comments, prepended data and ZIP64 cannot make preflight inspect a different
    directory. This is the sole dependency on zipfile's private layout constants;
    regression fixtures cover those formats. The reader reads only fixed-size
    ZIP64 records and the final 64 KiB comment window, not the directory itself.
    """
    try:
        endrec = _zip_layout._EndRecData(io.BytesIO(raw))
    except OSError as exc:
        raise zipfile.BadZipFile("Invalid ZIP end record.") from exc
    if endrec is None:
        raise zipfile.BadZipFile("Missing ZIP directory.")
    if endrec[_zip_layout._ECD_DISK_NUMBER] or endrec[_zip_layout._ECD_DISK_START]:
        raise zipfile.BadZipFile("Split ZIP archives are not supported.")
    declared_count = endrec[_zip_layout._ECD_ENTRIES_TOTAL]
    size = endrec[_zip_layout._ECD_SIZE]
    # Metadata consumes the same bounded archive bytes, never a separately
    # trusted allocation size. Preserve existing limits for otherwise valid ZIPs.
    if declared_count > max_entries or size > limits.MAX_ZIP_BYTES:
        raise ZipDirectoryTooLarge()
    end = endrec[_zip_layout._ECD_LOCATION]
    if endrec[_zip_layout._ECD_SIGNATURE] == _zip_layout.stringEndArchive64:
        # Patched CPython readers normalize this to the ZIP64 end record. Older
        # readers leave it at the classic EOCD. Inspect the record, not a Python
        # version number, so backported fixes cannot subtract the footer twice.
        if raw[end : end + 4] == b"PK\x05\x06":
            end -= _zip_layout.sizeEndCentDir64 + _zip_layout.sizeEndCentDir64Locator
        if raw[end : end + 4] != _zip_layout.stringEndArchive64:
            raise zipfile.BadZipFile("Invalid ZIP64 directory bounds.")
    start = end - size
    if start < 0 or end > len(raw) or endrec[_zip_layout._ECD_OFFSET] > start:
        raise zipfile.BadZipFile("Invalid ZIP directory bounds.")
    count = 0
    position = start
    while position < end:
        if (
            position + _zip_layout.sizeCentralDir > end
            or raw[position : position + 4] != _zip_layout.stringCentralDir
        ):
            raise zipfile.BadZipFile("Invalid ZIP directory record.")
        count += 1
        if count > max_entries:
            raise ZipDirectoryTooLarge()
        # Fixed header followed by filename, extra fields and entry comment. Do
        # not decode/copy these attacker-controlled strings during preflight.
        name_size, extra_size, comment_size = struct.unpack_from(
            "<HHH", raw, position + 28
        )
        position += _zip_layout.sizeCentralDir + name_size + extra_size + comment_size
        if position > end:
            raise zipfile.BadZipFile("Truncated ZIP directory record.")
    if count != declared_count or count != endrec[_zip_layout._ECD_ENTRIES_THIS_DISK]:
        raise zipfile.BadZipFile("ZIP directory count mismatch.")
    return count
