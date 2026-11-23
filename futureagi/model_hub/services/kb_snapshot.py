from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from minio.error import S3Error

from model_hub.models.choices import StatusType
from model_hub.services.document_text import (
    MAX_DOCUMENT_BYTES,
    SUPPORTED_DOCUMENT_SUFFIXES,
    DocumentTextError,
    extract_document_text,
)
from model_hub.utils.kb_download import (
    authorized_knowledge_base,
    knowledge_base_object_key,
)
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import get_storage_client

MAX_SELECTION_FILES = 100
MAX_SELECTION_BYTES = 200 * 1024 * 1024


class KnowledgeBaseSnapshotError(ValueError):
    def __init__(self, code: str, message: str, *, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class CapturedKnowledgeBaseSource:
    source_id: str
    filename: str
    media_type: str
    role: str
    content: bytes
    original_content: bytes
    origin: dict[str, Any]


def _read_object(key: str) -> bytes:
    try:
        response = get_storage_client().get_object(UPLOAD_BUCKET_NAME, key)
    except S3Error as exc:
        raise KnowledgeBaseSnapshotError(
            "source_content_unavailable",
            "Knowledge Base content could not be captured.",
            status_code=409,
        ) from exc
    try:
        content = response.read(MAX_DOCUMENT_BYTES + 1)
    finally:
        response.close()
        response.release_conn()
    if len(content) > MAX_DOCUMENT_BYTES:
        raise KnowledgeBaseSnapshotError(
            "source_limit_exceeded",
            "A selected file exceeds the Environment import limit.",
            status_code=413,
        )
    return content


def capture_knowledge_base_selection(
    request,
    *,
    kb_id: str,
    file_ids: list[str] | None,
    whole_collection: bool,
) -> tuple[CapturedKnowledgeBaseSource, ...]:
    """Authorize and capture one explicit file selection or one whole collection."""
    if whole_collection == (file_ids is not None):
        raise KnowledgeBaseSnapshotError(
            "source_request_invalid",
            "Choose either a whole Knowledge Base or an explicit file selection.",
        )
    kb = authorized_knowledge_base(request, kb_id)
    if kb.status != StatusType.COMPLETED.value:
        raise KnowledgeBaseSnapshotError(
            "source_not_ready",
            "The Knowledge Base is not ready to import.",
            status_code=409,
        )
    files = kb.files.filter(deleted=False)
    if file_ids is not None:
        requested = {str(identifier) for identifier in file_ids}
        files = files.filter(id__in=requested)
    selected = sorted(files, key=lambda item: str(item.id))
    if file_ids is not None and len(selected) != len(requested):
        raise KnowledgeBaseSnapshotError(
            "source_access_denied",
            "A selected Knowledge Base file is unavailable.",
            status_code=404,
        )
    if not selected:
        raise KnowledgeBaseSnapshotError(
            "source_not_found",
            "No Knowledge Base files were selected.",
            status_code=404,
        )
    if len(selected) > MAX_SELECTION_FILES:
        raise KnowledgeBaseSnapshotError(
            "source_limit_exceeded",
            "The selection exceeds the 100-file Environment import limit.",
            status_code=413,
        )

    captured = []
    total_bytes = 0
    for index, file in enumerate(selected, 1):
        if file.status != StatusType.COMPLETED.value:
            raise KnowledgeBaseSnapshotError(
                "source_not_ready",
                "A selected Knowledge Base file is not ready to import.",
                status_code=409,
            )
        suffix = PurePath(file.name).suffix.lower()
        if suffix not in SUPPORTED_DOCUMENT_SUFFIXES:
            raise KnowledgeBaseSnapshotError(
                "source_format_unsupported",
                "A selected Knowledge Base file is not Environment-importable.",
            )
        original = _read_object(knowledge_base_object_key(kb, file))
        total_bytes += len(original)
        if total_bytes > MAX_SELECTION_BYTES:
            raise KnowledgeBaseSnapshotError(
                "source_limit_exceeded",
                "The selection exceeds the 200 MiB Environment import limit.",
                status_code=413,
            )
        original_sha256 = "sha256:" + hashlib.sha256(original).hexdigest()
        try:
            if suffix == ".pdf":
                parsed = original
                filename = f"source-{index}.pdf"
                media_type = "application/pdf"
            else:
                parsed = extract_document_text(original, file.name).encode("utf-8")
                filename = f"source-{index}.txt"
                media_type = "text/plain"
        except DocumentTextError as exc:
            raise KnowledgeBaseSnapshotError(
                "source_content_unavailable", str(exc), status_code=409
            ) from exc
        parsed_sha256 = "sha256:" + hashlib.sha256(parsed).hexdigest()
        source_id = (
            "src_"
            + hashlib.sha256(
                f"{kb.id}:{file.id}:{original_sha256}".encode()
            ).hexdigest()[:24]
        )
        captured.append(
            CapturedKnowledgeBaseSource(
                source_id=source_id,
                filename=filename,
                media_type=media_type,
                role="reference",
                content=parsed,
                original_content=original,
                origin={
                    "source_id": source_id,
                    "kind": "knowledge_base",
                    "kb_id": str(kb.id),
                    "file_id": str(file.id),
                    "original_name": file.name,
                    "original_sha256": original_sha256,
                    "parsed_sha256": parsed_sha256,
                    "upstream_revision": None,
                },
            )
        )
    return tuple(captured)
