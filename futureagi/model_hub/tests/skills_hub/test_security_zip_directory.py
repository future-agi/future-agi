"""Reject metadata amplification before the v1 importer constructs a ZipFile."""

import io
import struct
import zipfile
from unittest.mock import Mock

import pytest

from model_hub.services.skills_hub import limits, packages, zip_directory
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.services.skills_hub.zip_directory import (
    ZipDirectoryTooLarge,
    preflight_zip_directory,
)

ROOT = "repo-" + "a" * 7
SKILL = b"---\nname: sample\ndescription: Use when testing bounded archive parsing and unchanged content.\n---\nFollow the policy.\n"


def archive(count, *, entry_comment=b"", comment=b""):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for index in range(count):
            entry = zipfile.ZipInfo(f"{ROOT}/{index:06}")
            entry.comment = entry_comment
            zipped.writestr(entry, b"")
        zipped.comment = comment
    return output.getvalue()


def set_declared_count(raw, count):
    data = bytearray(raw)
    offset = data.rfind(b"PK\x05\x06")
    struct.pack_into("<HH", data, offset + 8, count, count)
    return bytes(data)


def read_archive(raw):
    return packages.parse_upload([("synthetic.zip", raw)])


@pytest.mark.parametrize("kind", ["many_entries", "lying_count", "oversized_metadata"])
def test_rejects_before_zipinfo_materialization(kind, monkeypatch):
    if kind == "many_entries":
        # The reported exploit: valid, empty entries below the compressed-byte cap.
        raw = archive(60_000)
    elif kind == "lying_count":
        raw = set_declared_count(archive(limits.MAX_ZIP_ENTRIES + 1), 1)
    else:
        raw = bytearray(archive(1))
        end = raw.rfind(b"PK\x05\x06")
        struct.pack_into("<I", raw, end + 12, limits.MAX_ZIP_BYTES + 1)
        raw = bytes(raw)
    assert len(raw) < limits.MAX_ZIP_BYTES
    constructor = Mock(
        side_effect=AssertionError("ZipFile eagerly allocates unbounded metadata")
    )
    monkeypatch.setattr(zipfile, "ZipFile", constructor)
    with pytest.raises(SkillsHubError) as error:
        read_archive(raw)
    assert error.value.status == 413
    assert error.value.code == "payload_too_large"
    constructor.assert_not_called()


def test_multiple_uploads_share_entry_budget_before_constructing_next_archive(
    monkeypatch,
):
    first, second = archive(600), archive(401)
    constructor = Mock(wraps=zipfile.ZipFile)
    monkeypatch.setattr(zipfile, "ZipFile", constructor)
    with pytest.raises(SkillsHubError) as error:
        packages.parse_upload([("one.zip", first), ("two.zip", second)])
    assert error.value.code == "payload_too_large"
    assert constructor.call_count == 1


@pytest.mark.parametrize("count", [0, 1, limits.MAX_ZIP_ENTRIES])
def test_normal_count_and_comment_boundaries(count):
    raw = archive(count, comment=b"comment" * 9362)
    assert preflight_zip_directory(raw, max_entries=limits.MAX_ZIP_ENTRIES) == count
    with zipfile.ZipFile(io.BytesIO(raw)) as zipped:
        assert len(zipped.infolist()) == count


@pytest.mark.parametrize("prefix", [b"", b"synthetic self-extracting prefix\n"])
@pytest.mark.parametrize("zip64", [False, True])
def test_valid_zip64_and_prepended_archives_inspect_same_directory(
    prefix, zip64, monkeypatch
):
    with monkeypatch.context() as context:
        if zip64:
            context.setattr(zipfile, "ZIP64_LIMIT", 0)
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as zipped:
            zipped.writestr(ROOT + "/SKILL.md", SKILL)
            zipped.comment = b"synthetic comment"
        raw = prefix + stream.getvalue()
    assert preflight_zip_directory(raw, max_entries=1) == 1
    uploaded = read_archive(raw)
    assert uploaded["rejected"] == []
    assert uploaded["bundles"][0]["files"][0]["content"].encode() == SKILL


@pytest.mark.parametrize("prefix", [b"", b"synthetic prefix\n"])
@pytest.mark.parametrize("location_signature", [b"PK\x05\x06", b"PK\x06\x06"])
def test_zip64_accepts_legacy_and_normalized_reader_locations(
    prefix, location_signature, monkeypatch
):
    with monkeypatch.context() as context:
        context.setattr(zipfile, "ZIP64_LIMIT", 0)
        raw = prefix + archive(1)
    metadata = zip_directory._zip_layout._EndRecData(io.BytesIO(raw))
    assert metadata is not None
    metadata[zip_directory._zip_layout._ECD_LOCATION] = raw.rfind(location_signature)
    monkeypatch.setattr(zipfile, "_EndRecData", lambda _stream: metadata)
    constructor = Mock(side_effect=AssertionError("Do not allocate ZipInfo objects"))
    monkeypatch.setattr(zipfile, "ZipFile", constructor)
    assert preflight_zip_directory(raw, max_entries=1) == 1
    constructor.assert_not_called()


def test_zip64_count_is_bounded_before_construction(monkeypatch):
    with monkeypatch.context() as context:
        context.setattr(zipfile, "ZIP_FILECOUNT_LIMIT", 1)
        raw = archive(limits.MAX_ZIP_ENTRIES + 1)
    constructor = Mock(side_effect=AssertionError("ZIP64 count must be preflighted"))
    monkeypatch.setattr(zipfile, "ZipFile", constructor)
    with pytest.raises(ZipDirectoryTooLarge):
        preflight_zip_directory(raw, max_entries=limits.MAX_ZIP_ENTRIES)
    constructor.assert_not_called()


@pytest.mark.parametrize(
    "kind",
    ["count_mismatch", "truncated_record", "bad_signature", "bad_bounds", "split_disk"],
)
def test_malformed_directory_is_rejected_without_constructing_archive(
    kind, monkeypatch
):
    raw = bytearray(archive(1))
    end = raw.rfind(b"PK\x05\x06")
    central = raw.find(b"PK\x01\x02")
    if kind == "count_mismatch":
        struct.pack_into("<HH", raw, end + 8, 0, 0)
    elif kind == "truncated_record":
        struct.pack_into("<H", raw, central + 28, 65535)
    elif kind == "bad_signature":
        raw[central] = 0
    elif kind == "bad_bounds":
        struct.pack_into("<I", raw, end + 16, len(raw) + 1)
    else:
        struct.pack_into("<H", raw, end + 4, 1)
    constructor = Mock(
        side_effect=AssertionError("Malformed directory must not materialize")
    )
    monkeypatch.setattr(zipfile, "ZipFile", constructor)
    result = read_archive(bytes(raw))
    assert result["bundles"] == []
    assert result["rejected"] == [
        {"path": "synthetic.zip", "reason": "archive_invalid"}
    ]
    constructor.assert_not_called()
