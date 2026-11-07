"""Upload resource limits must stop reads/storage, not merely return a late 413."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.core.files.uploadhandler import (
    MemoryFileUploadHandler,
    TemporaryFileUploadHandler,
)
from rest_framework.test import APIRequestFactory, force_authenticate

from model_hub.services.skills_hub import limits
from model_hub.services.skills_hub.errors import SkillsHubError
from model_hub.views import skills_hub as views

BOUNDARY = "synthetic-skills-stream-boundary"
MEDIA_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
CHUNK = 64 * 1024


class LazyBody:
    """Stream synthetic large bodies without allocating them or touching sockets."""

    def __init__(self, parts):
        self.parts = parts  # bytes or an integer number of repeated payload bytes
        self.length = sum(
            part if isinstance(part, int) else len(part) for part in parts
        )
        self.index = self.offset = self.bytes_read = 0

    def read(self, size=-1):
        assert 0 <= size <= CHUNK, "Multipart parsing must use bounded reads"
        # Fail safely even if a regression starts consuming a 900 MiB request.
        assert self.bytes_read <= limits.MAX_MULTIPART_BYTES + 2 * CHUNK, (
            "Oversized body was drained"
        )
        chunks = []
        while size and self.index < len(self.parts):
            part = self.parts[self.index]
            length = part if isinstance(part, int) else len(part)
            take = min(size, length - self.offset)
            chunks.append(
                b"x" * take
                if isinstance(part, int)
                else part[self.offset : self.offset + take]
            )
            self.offset += take
            size -= take
            self.bytes_read += take
            if self.offset == length:
                self.index += 1
                self.offset = 0
        return b"".join(chunks)


def multipart_parts(files, *, field_bytes=0):
    parts = []
    for filename, size in files:
        parts.extend(
            [
                (
                    f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                    "Content-Type: application/octet-stream\r\n\r\n"
                ).encode(),
                size,
                b"\r\n",
            ]
        )
    if field_bytes:
        parts.extend(
            [
                f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="selected"\r\n\r\n'.encode(),
                field_bytes,
                b"\r\n",
            ]
        )
    parts.append(f"--{BOUNDARY}--\r\n".encode())
    return parts


def parse(stream, *, declared=None):
    request = SimpleNamespace(
        META={
            "CONTENT_TYPE": MEDIA_TYPE,
            "CONTENT_LENGTH": str(stream.length if declared is None else declared),
        }
    )
    # Supply the vulnerable parser's normal handler chain too, so regression
    # tests exercise its old behavior rather than fail on a missing attribute.
    request.upload_handlers = [
        MemoryFileUploadHandler(request),
        TemporaryFileUploadHandler(request),
    ]
    return views.SkillMultipartParser().parse(
        stream, MEDIA_TYPE, {"request": request, "encoding": "utf-8"}
    )


@pytest.fixture(autouse=True)
def upload_settings(settings, tmp_path):
    settings.FILE_UPLOAD_TEMP_DIR = str(tmp_path)
    settings.FILE_UPLOAD_MAX_MEMORY_SIZE = 1024**3
    settings.DATA_UPLOAD_MAX_MEMORY_SIZE = 1024**3
    return tmp_path


def assert_large(error):
    assert error.value.status == 413
    assert error.value.code == "payload_too_large"


def test_900_mib_content_length_is_rejected_before_reading_any_body(upload_settings):
    body = LazyBody(multipart_parts([("huge.zip", 900 * 1024 * 1024)]))
    with pytest.raises(SkillsHubError) as error:
        parse(body)
    assert_large(error)
    assert body.bytes_read == 0
    assert list(upload_settings.iterdir()) == []


@pytest.mark.parametrize(
    "filename,maximum",
    [("large.zip", limits.MAX_ZIP_BYTES), ("large.txt", limits.MAX_FILE_BYTES)],
)
def test_file_limit_aborts_during_parsing_before_full_body(
    filename, maximum, upload_settings
):
    body = LazyBody(multipart_parts([(filename, maximum + 3 * CHUNK)]))
    with pytest.raises(SkillsHubError) as error:
        parse(body)
    assert_large(error)
    assert body.bytes_read <= maximum + 2 * CHUNK
    assert body.bytes_read < body.length
    assert list(upload_settings.iterdir()) == []


def test_aggregate_file_limit_closes_completed_and_partial_uploads(upload_settings):
    body = LazyBody(multipart_parts([(f"{i}.zip", 8 * 1024 * 1024) for i in range(3)]))
    with pytest.raises(SkillsHubError) as error:
        parse(body, declared=1)
    assert_large(error)
    assert body.bytes_read <= limits.MAX_UNZIPPED_BYTES + 2 * CHUNK
    assert body.bytes_read < body.length
    assert list(upload_settings.iterdir()) == []


@pytest.mark.parametrize("kind", ["field", "preamble", "epilogue"])
def test_whole_multipart_budget_is_enforced_even_with_false_content_length(
    kind, upload_settings
):
    oversize = limits.MAX_MULTIPART_BYTES + 4 * CHUNK
    if kind == "field":
        parts = multipart_parts([], field_bytes=oversize)
    elif kind == "preamble":
        parts = [oversize, *multipart_parts([])]
    else:
        parts = [*multipart_parts([("done.txt", 5)]), oversize]
    body = LazyBody(parts)
    with pytest.raises(SkillsHubError) as error:
        parse(body, declared=1)
    assert_large(error)
    assert body.bytes_read == limits.MAX_MULTIPART_BYTES + 1
    assert body.bytes_read < body.length
    assert list(upload_settings.iterdir()) == []


@pytest.mark.parametrize(
    "filename,maximum",
    [("ok.zip", limits.MAX_ZIP_BYTES), ("ok.txt", limits.MAX_FILE_BYTES)],
)
def test_exact_file_limit_spools_with_bounded_memory_and_preserves_paths(
    filename, maximum, settings
):
    body = LazyBody(multipart_parts([("folder/" + filename, maximum)]))
    parsed = parse(body)
    upload = parsed.files.getlist("files")[0]
    try:
        assert isinstance(upload, TemporaryUploadedFile)
        assert upload.size == maximum
        assert upload.skill_relative_path == "folder/" + filename
        assert upload.read(8) == b"x" * 8
        assert settings.FILE_UPLOAD_MAX_MEMORY_SIZE == 1024**3
    finally:
        upload.close()


def test_exact_aggregate_and_wire_limits_are_accepted():
    files = [("first.zip", limits.MAX_ZIP_BYTES), ("second.zip", limits.MAX_ZIP_BYTES)]
    parts = multipart_parts(files)
    length = sum(part if isinstance(part, int) else len(part) for part in parts)
    # Legal epilogue consumes the remaining wire budget, without increasing files.
    body = LazyBody([*parts, limits.MAX_MULTIPART_BYTES - length])
    parsed = parse(body)
    try:
        assert (
            sum(upload.size for upload in parsed.files.getlist("files"))
            == limits.MAX_UNZIPPED_BYTES
        )
        assert body.bytes_read == limits.MAX_MULTIPART_BYTES
    finally:
        for upload in parsed.files.getlist("files"):
            upload.close()


def test_loose_file_count_is_bounded_before_storing_excess_file(
    monkeypatch, upload_settings
):
    monkeypatch.setattr(limits, "MAX_LOOSE_FILES", 2)
    body = LazyBody(multipart_parts([(f"{i}.txt", 1) for i in range(3)]))
    with pytest.raises(SkillsHubError) as error:
        parse(body)
    assert_large(error)
    assert list(upload_settings.iterdir()) == []


@pytest.mark.django_db
@pytest.mark.parametrize("view", [views.SkillImportPreviewView, views.SkillImportView])
def test_both_import_routes_return_structured_413_before_package_parsing(
    user, organization, workspace, view, monkeypatch
):
    parse_upload = Mock(
        side_effect=AssertionError("Package parser must not see oversized uploads")
    )
    monkeypatch.setattr(views.packages, "parse_upload", parse_upload)
    request = APIRequestFactory().post(
        "/model-hub/skills/import/",
        {
            "files": SimpleUploadedFile(
                "large.txt", b"x" * (limits.MAX_FILE_BYTES + 1)
            ),
        },
        format="multipart",
    )
    force_authenticate(request, user=user)
    request.organization = organization
    request.workspace = workspace
    response = view.as_view()(request)
    assert response.status_code == 413, response.data
    assert response.data["code"] == "payload_too_large"
    parse_upload.assert_not_called()
