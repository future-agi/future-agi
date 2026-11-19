"""Endpoint-scoped multipart limits, enforced before Django stores upload bytes."""

from __future__ import annotations

from typing import IO, cast

from django.core.files.uploadedfile import UploadedFile
from django.core.files.uploadhandler import (
    FileUploadHandler,
    TemporaryFileUploadHandler,
)
from django.http.multipartparser import MultiPartParser as DjangoMultipartParser
from django.http.multipartparser import MultiPartParserError
from django.utils.datastructures import MultiValueDict
from rest_framework.exceptions import ParseError
from rest_framework.parsers import DataAndFiles, MultiPartParser

from model_hub.services.skills_hub import limits
from model_hub.services.skills_hub.errors import SkillsHubError


def _too_large() -> SkillsHubError:
    return SkillsHubError(
        "payload_too_large",
        "The upload exceeds the file or import size limit.",
        status=413,
    )


class BoundedMultipartStream:
    """Count all wire bytes, including fields, headers, preamble and epilogue.

    Content-Length is only an early rejection hint. Capped reads also protect
    against an inaccurate length without ever draining an oversized request.
    """

    def __init__(self, stream):
        self.stream = stream
        self.remaining = limits.MAX_MULTIPART_BYTES

    def read(self, size=-1):
        size = min(size, self.remaining + 1) if size >= 0 else self.remaining + 1
        data = self.stream.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise _too_large()
        return data


class SkillsUploadHandler(FileUploadHandler):
    """Reject a chunk before downstream handlers can buffer or spool it."""

    chunk_size = 64 * 1024

    def __init__(self, request):
        super().__init__(request)
        self.total_bytes = self.file_count = self.loose_count = 0
        self.file_bytes = 0
        self.maximum = limits.MAX_FILE_BYTES

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        is_zip = (self.file_name or "").lower().endswith(".zip")
        self.maximum = limits.MAX_ZIP_BYTES if is_zip else limits.MAX_FILE_BYTES
        self.file_bytes = 0
        self.file_count += 1
        self.loose_count += not is_zip
        if (
            self.file_count > limits.MAX_UPLOAD_FILES
            or self.loose_count > limits.MAX_LOOSE_FILES
        ):
            raise _too_large()

    def receive_data_chunk(self, raw_data, start):
        self.file_bytes += len(raw_data)
        self.total_bytes += len(raw_data)
        if (
            self.file_bytes > self.maximum
            or self.total_bytes > limits.MAX_UNZIPPED_BYTES
        ):
            # Do not use StopUpload: Django may swallow it and drain the body.
            raise _too_large()
        return raw_data

    def file_complete(self, file_size):
        return None


class RelativePathMultipartParser(DjangoMultipartParser):
    """Preserve logical paths, never use them as temporary storage filenames."""

    _files: MultiValueDict[str, UploadedFile]

    def sanitize_file_name(self, file_name):
        self.skill_relative_path = file_name
        return super().sanitize_file_name(file_name) or "invalid-upload"

    def handle_file_complete(self, old_field_name, counters):
        before = len(self._files.getlist(old_field_name))
        super().handle_file_complete(old_field_name, counters)
        uploads = self._files.getlist(old_field_name)
        if len(uploads) > before:
            uploads[-1].skill_relative_path = self.skill_relative_path  # type: ignore[attr-defined]


class SkillMultipartParser(MultiPartParser):
    def parse(self, stream, media_type=None, parser_context=None):
        context = parser_context or {}
        request = context["request"]
        meta = request.META.copy()
        meta["CONTENT_TYPE"] = media_type
        try:
            length = int(meta.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError) as exc:
            raise ParseError("Invalid multipart upload.") from exc
        if length > limits.MAX_MULTIPART_BYTES:
            raise _too_large()
        # These handlers apply only to Skills imports. Do not inherit the global
        # 1 GiB in-memory threshold or mutate settings used by concurrent requests.
        storage = TemporaryFileUploadHandler(request)
        handlers = [SkillsUploadHandler(request), storage]
        try:
            data, files = RelativePathMultipartParser(
                meta,
                # Django only reads this adapter; its stub requires the full IO API.
                cast(IO[bytes], BoundedMultipartStream(stream)),
                handlers,
                context.get("encoding"),
            ).parse()
        except Exception as exc:
            # Django closes completed files on failure; the current partial file
            # isn't in that collection yet. Cleanup must also run on parser errors.
            if getattr(storage, "file", None) is not None:
                storage.file.close()
            if isinstance(exc, MultiPartParserError):
                raise ParseError("Invalid multipart upload.") from exc
            raise
        return DataAndFiles(data, files)
