"""Authenticated delivery of files belonging to a knowledge-base collection."""

import mimetypes
import re
from collections.abc import Iterator
from typing import cast
from uuid import UUID

from accounts.models.organization_membership import OrganizationMembership
from accounts.models.workspace import WorkspaceMembership
from asgiref.sync import sync_to_async
from django.conf import settings
from django.http import StreamingHttpResponse
from django.utils.http import content_disposition_header
from minio.error import S3Error
from model_hub.models.choices import StatusType
from model_hub.models.develop_dataset import KnowledgeBaseFile
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from tfc.constants.levels import Level
from tfc.utils.storage_client import get_storage_client


class _StorageDownload:
    def __init__(self, response):
        self.response = response

    def __iter__(self):
        return self.response.stream(64 * 1024)

    def close(self):
        try:
            self.response.close()
        finally:
            self.response.release_conn()


_STREAM_END = object()


def _next_chunk(chunks: Iterator[bytes]) -> bytes | object:
    return next(chunks, _STREAM_END)


class _StorageStreamingResponse(StreamingHttpResponse):
    """Stream storage chunks one at a time under both WSGI and ASGI.

    Django's ASGI handler collects a synchronous iterator into a list before
    sending the body. Reading one chunk per worker-thread call keeps memory
    bounded by the chunk size instead of the file size.

    Reads run on the request's thread-sensitive worker, the same one Django
    uses for response.close(), so a close after a client disconnect waits for
    the read in progress instead of releasing the connection during it.
    """

    async def __aiter__(self):
        # _StorageDownload only supplies synchronous byte chunks.
        chunks = iter(cast(Iterator[bytes], self.streaming_content))
        read_chunk = sync_to_async(_next_chunk)
        while True:
            chunk = await read_chunk(chunks)
            if chunk is _STREAM_END:
                return
            yield chunk


def download_knowledge_base_file(request, kb_id, file_id):
    """Authorize the collection and its file before touching object storage."""
    try:
        kb_id = UUID(str(kb_id))
    except (ValueError, TypeError) as exc:
        raise ValidationError("Invalid knowledge base ID.") from exc

    org = getattr(request, "organization", None)
    workspace = getattr(request, "workspace", None)
    if org is None or workspace is None or workspace.organization_id != org.id:
        raise PermissionDenied("Access denied to this knowledge base.")
    for header, selected in (
        ("X-Organization-Id", org.id),
        ("X-Workspace-Id", workspace.id),
    ):
        if request.headers.get(header) and request.headers[header] != str(selected):
            raise PermissionDenied("Access denied to this knowledge base.")

    membership = OrganizationMembership.no_workspace_objects.filter(
        user=request.user,
        user__is_active=True,
        organization=org,
        is_active=True,
        deleted=False,
    ).first()
    if membership is None or not workspace.is_active or workspace.deleted:
        raise PermissionDenied("Access denied to this knowledge base.")
    if membership.level_or_legacy < Level.VIEWER:
        raise PermissionDenied("Access denied to this knowledge base.")
    if membership.level_or_legacy < Level.ADMIN:
        workspace_membership = WorkspaceMembership.no_workspace_objects.filter(
            user=request.user,
            workspace=workspace,
            is_active=True,
            deleted=False,
        ).first()
        if (
            workspace_membership is None
            or (
                workspace_membership.level
                if workspace_membership.level is not None
                else Level.STRING_TO_LEVEL.get(workspace_membership.role, 0)
            )
            < Level.WORKSPACE_VIEWER
        ):
            raise PermissionDenied("Access denied to this knowledge base.")

    # The file UI uses KnowledgeBaseFile, not the separate KnowledgeBase model.
    kb = KnowledgeBaseFile.no_workspace_objects.filter(
        id=kb_id,
        organization=org,
        deleted=False,
    ).first()
    if kb is None or (
        kb.workspace_id != workspace.id
        and not (kb.workspace_id is None and workspace.is_default)
    ):
        raise PermissionDenied("Access denied to this knowledge base.")
    file = (
        kb.files.filter(id=file_id, deleted=False)
        .exclude(status=StatusType.DELETING.value)
        .first()
    )
    if file is None:
        raise NotFound("Knowledge base file not found.")

    # Keys follow upload_file_to_s3: UUID/UUID.extension. Never accept an object
    # key or a storage URL from the caller, including an uploaded_url override.
    extension = file.name.rsplit(".", 1)[-1].lower()
    if not re.fullmatch(r"[a-z0-9]{1,10}", extension):
        raise NotFound("Knowledge base file not found.")
    filename = file.name.replace("\\", "/").rsplit("/", 1)[-1]
    filename = "".join(
        char for char in filename if ord(char) >= 32 and ord(char) != 127
    )
    filename = filename or f"download.{extension}"
    key = f"knowledge-base/{kb.id}/{file.id}.{extension}"
    try:
        stored = get_storage_client().get_object(settings.UPLOAD_BUCKET_NAME, key)
    except S3Error as exc:
        if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
            raise NotFound("Knowledge base file not found.") from exc
        raise
    response = _StorageStreamingResponse(
        _StorageDownload(stored),
        content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream",
    )
    disposition = content_disposition_header(True, filename)
    assert disposition is not None  # The sanitized filename is nonempty.
    response["Content-Disposition"] = disposition
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
