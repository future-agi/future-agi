import hashlib
import io
import uuid
from uuid import UUID

import structlog
from django.db import IntegrityError, transaction
from minio.error import S3Error
from urllib3.exceptions import HTTPError

from simulate.models import CodeArchive
from tfc.settings.settings import UPLOAD_BUCKET_NAME
from tfc.utils.storage_client import ensure_bucket, get_storage_client

logger = structlog.get_logger(__name__)

_STORAGE_ERRORS = (S3Error, HTTPError)


def stored_commit_archive(
    *, organization_id: UUID, repository: str, commit_sha: str
) -> bytes | None:
    archive = CodeArchive.no_workspace_objects.filter(
        organization_id=organization_id,
        repository=repository.lower(),
        commit_sha=commit_sha.lower(),
    ).first()
    if archive is None:
        return None
    response = None
    try:
        response = get_storage_client().get_object(
            UPLOAD_BUCKET_NAME, archive.object_key
        )
        body = response.read()
    except _STORAGE_ERRORS:
        logger.warning(
            "code_archive_unreadable", code_archive_id=str(archive.id), exc_info=True
        )
        return None
    finally:
        if response is not None:
            response.close()
            response.release_conn()
    if hashlib.sha256(body).hexdigest() != archive.sha256:
        logger.warning("code_archive_digest_mismatch", code_archive_id=str(archive.id))
        return None
    return body


def store_commit_archive(
    *, organization_id: UUID, repository: str, commit_sha: str, body: bytes
) -> None:
    key = f"code-archives/{organization_id}/{uuid.uuid4()}.tar.gz"
    client = get_storage_client()
    try:
        ensure_bucket(client, UPLOAD_BUCKET_NAME)
        client.put_object(
            bucket_name=UPLOAD_BUCKET_NAME,
            object_name=key,
            data=io.BytesIO(body),
            length=len(body),
            content_type="application/gzip",
        )
    except _STORAGE_ERRORS:
        logger.warning("code_archive_not_stored", repository=repository, exc_info=True)
        return
    identity = {
        "organization_id": organization_id,
        "repository": repository.lower(),
        "commit_sha": commit_sha.lower(),
    }
    contents = {
        "object_key": key,
        "sha256": hashlib.sha256(body).hexdigest(),
        "size_bytes": len(body),
        "deleted": False,
        "deleted_at": None,
    }
    try:
        with transaction.atomic():
            existing = (
                CodeArchive.all_objects.select_for_update().filter(**identity).first()
            )
            if existing is None:
                CodeArchive.all_objects.create(**identity, **contents)
                return
            replaced_key = existing.object_key
            for field, value in contents.items():
                setattr(existing, field, value)
            existing.save(update_fields=[*contents, "updated_at"])
    except IntegrityError:
        replaced_key = key
    _remove_object(client, replaced_key)


def _remove_object(client, key: str) -> None:
    try:
        client.remove_object(UPLOAD_BUCKET_NAME, key)
    except _STORAGE_ERRORS:
        logger.warning("code_archive_orphan_left", object_key=key, exc_info=True)
