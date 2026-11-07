"""Bound ZIP metadata before ZipFile eagerly allocates its ZipInfo inventory."""

from __future__ import annotations

import io
import struct
import zipfile

from model_hub.services.skills_hub import limits


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
        endrec = zipfile._EndRecData(io.BytesIO(raw))
    except OSError as exc:
        raise zipfile.BadZipFile("Invalid ZIP end record.") from exc
    if endrec is None:
        raise zipfile.BadZipFile("Missing ZIP directory.")
    if endrec[zipfile._ECD_DISK_NUMBER] or endrec[zipfile._ECD_DISK_START]:
        raise zipfile.BadZipFile("Split ZIP archives are not supported.")
    declared_count = endrec[zipfile._ECD_ENTRIES_TOTAL]
    size = endrec[zipfile._ECD_SIZE]
    # Metadata consumes the same bounded archive bytes, never a separately
    # trusted allocation size. Preserve existing limits for otherwise valid ZIPs.
    if declared_count > max_entries or size > limits.MAX_ZIP_BYTES:
        raise ZipDirectoryTooLarge()
    end = endrec[zipfile._ECD_LOCATION]
    if endrec[zipfile._ECD_SIGNATURE] == zipfile.stringEndArchive64:
        end -= zipfile.sizeEndCentDir64 + zipfile.sizeEndCentDir64Locator
    start = end - size
    if start < 0 or end > len(raw) or endrec[zipfile._ECD_OFFSET] > start:
        raise zipfile.BadZipFile("Invalid ZIP directory bounds.")
    count = 0
    position = start
    while position < end:
        if (
            position + zipfile.sizeCentralDir > end
            or raw[position : position + 4] != zipfile.stringCentralDir
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
        position += zipfile.sizeCentralDir + name_size + extra_size + comment_size
        if position > end:
            raise zipfile.BadZipFile("Truncated ZIP directory record.")
    if count != declared_count or count != endrec[zipfile._ECD_ENTRIES_THIS_DISK]:
        raise zipfile.BadZipFile("ZIP directory count mismatch.")
    return count
