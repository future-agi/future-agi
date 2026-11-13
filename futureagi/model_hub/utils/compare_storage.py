"""Dataset comparison data in the private compare bucket.

A comparison keeps its pages (``page_<n>.json``) and its metadata
(``metadata.json``) in object storage under ``compare/<id>/`` so that every
backend replica can serve it. They hold dataset cells, so they live in
``COMPARE_BUCKET_NAME``: a bucket with no anonymous policy, read and written
only through the backend's own credentials. No URL to these objects is ever
handed to a client.

A comparison expires ``COMPARE_MAX_AGE_SECONDS`` after it was created: its
metadata is no longer served, and the scheduled cleanup removes its objects.
"""

import io
import json
import os
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from django.conf import settings

from tfc.utils.storage import get_compare_local_dir, get_compare_local_root
from tfc.utils.storage_client import (
    PrivateBucketRefused,
    ensure_private_bucket,
    get_storage_client,
)

logger = structlog.get_logger(__name__)

COMPARE_PREFIX = "compare/"
COMPARE_METADATA_OBJECT = "metadata.json"
COMPARE_MAX_AGE_SECONDS = 2 * 60 * 60


class CompareObjectUnavailable(ValueError):
    """A comparison object that is missing or could not be read."""


def compare_bucket_name() -> str:
    """The private bucket for comparison data. The upload bucket is refused:
    anyone may read its objects by URL."""
    bucket = str(getattr(settings, "COMPARE_BUCKET_NAME", "") or "").strip()
    upload_bucket = str(getattr(settings, "UPLOAD_BUCKET_NAME", "") or "").strip()
    if not bucket or bucket.lower() == upload_bucket.lower():
        raise PrivateBucketRefused(bucket or "(unset)")
    return bucket


def _client_and_bucket():
    bucket = compare_bucket_name()
    client = get_storage_client()
    ensure_private_bucket(client, bucket)
    return client, bucket


def compare_object_key(compare_id, name: str) -> str:
    return f"{COMPARE_PREFIX}{compare_id}/{name}"


def write_compare_json(compare_id, name: str, data) -> None:
    """Store one comparison object. Raises when the bucket did not take it."""
    client, bucket = _client_and_bucket()
    payload = json.dumps(data).encode("utf-8")
    client.put_object(
        bucket_name=bucket,
        object_name=compare_object_key(compare_id, name),
        data=io.BytesIO(payload),
        length=len(payload),
        content_type="application/json",
    )


def read_compare_json(compare_id, name: str):
    """One comparison object, read with the backend's credentials. Raises
    CompareObjectUnavailable when it is missing or cannot be read."""
    response = None
    try:
        client, bucket = _client_and_bucket()
        response = client.get_object(bucket, compare_object_key(compare_id, name))
        return json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        logger.warning(
            "compare_object_unavailable",
            compare_id=str(compare_id),
            object_name=name,
            error=type(exc).__name__,
        )
        raise CompareObjectUnavailable(name) from exc
    finally:
        if response is not None:
            response.close()
            response.release_conn()


def delete_compare_objects(compare_id) -> None:
    """Remove every object of one comparison. Raises when listing or removal
    fails, so the caller can keep what lets it retry."""
    client, bucket = _client_and_bucket()
    _remove_prefix(client, bucket, compare_id)


def delete_compare_metadata(compare_id) -> None:
    """Remove a comparison's metadata, after which every read of the
    comparison answers as not found. Raises on a storage error; removing
    metadata that is already gone is not an error."""
    client, bucket = _client_and_bucket()
    client.remove_object(
        bucket, compare_object_key(compare_id, COMPARE_METADATA_OBJECT)
    )


def created_at_now() -> str:
    return datetime.now(UTC).isoformat()


def is_expired(metadata, now: datetime | None = None) -> bool:
    """Whether a comparison's metadata is past its age limit. Metadata without
    a readable creation time counts as expired."""
    try:
        created_at = datetime.fromisoformat(str(metadata["created_at"]))
    except (KeyError, TypeError, ValueError):
        return True
    if created_at.tzinfo is None:
        return True
    now = now or datetime.now(UTC)
    return now - created_at > timedelta(seconds=COMPARE_MAX_AGE_SECONDS)


def _is_compare_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _listed_compare_ids(client, bucket: str, now: datetime):
    """(id, newest modification time) for each comparison under compare/ in
    the bucket, yielded as the listing reaches the next id. Listings return
    keys in order, so one comparison's objects arrive together. An object
    without a modification time counts as new, so its comparison is kept."""
    current: str | None = None
    newest: datetime = now
    for obj in client.list_objects(bucket, prefix=COMPARE_PREFIX, recursive=True):
        compare_id, _, name = obj.object_name[len(COMPARE_PREFIX) :].partition("/")
        if not name or not _is_compare_id(compare_id):
            continue
        modified = getattr(obj, "last_modified", None) or now
        if compare_id != current:
            if current is not None:
                yield current, newest
            current, newest = compare_id, modified
        else:
            newest = max(newest, modified)
    if current is not None:
        yield current, newest


def _remove_prefix(client, bucket: str, compare_id) -> None:
    for obj in client.list_objects(
        bucket, prefix=compare_object_key(compare_id, ""), recursive=True
    ):
        client.remove_object(bucket, obj.object_name)


def remove_compare(compare_id) -> None:
    """Remove a comparison's objects, then its local folder. The local folder
    stays when the remote removal fails, so the local sweep retries it."""
    delete_compare_objects(compare_id)
    shutil.rmtree(get_compare_local_dir(compare_id), ignore_errors=True)


def sweep_local_compare_root(now: float | None = None) -> None:
    """Remove expired comparisons whose folder is under this process's local
    compare root."""
    compare_root = get_compare_local_root()
    try:
        folders = os.listdir(compare_root)
    except FileNotFoundError:
        return
    now = time.time() if now is None else now
    for folder in folders:
        folder_path = os.path.join(compare_root, folder)
        try:
            if not _is_compare_id(folder) or os.path.islink(folder_path):
                continue
            if not os.path.isdir(folder_path):
                continue
            if now - os.path.getctime(folder_path) <= COMPARE_MAX_AGE_SECONDS:
                continue
            remove_compare(folder)
            logger.info("compare_folder_deleted", folder=folder)
        except Exception:
            # The folder stays, so the next sweep retries it.
            logger.exception("compare_folder_delete_failed", folder=folder)


def _sweep_bucket(client, bucket: str, deadline: float, now: datetime) -> None:
    """Remove the expired comparisons in one bucket while listing it, so that
    a slow listing still removes what it reached. Stops at the deadline
    (``time.monotonic()``); the next run lists the bucket again."""
    cutoff = now - timedelta(seconds=COMPARE_MAX_AGE_SECONDS)
    try:
        for compare_id, modified in _listed_compare_ids(client, bucket, now):
            if time.monotonic() >= deadline:
                logger.warning("compare_objects_sweep_deferred", bucket=bucket)
                return
            if modified >= cutoff:
                continue
            try:
                _remove_prefix(client, bucket, compare_id)
                logger.info("compare_objects_deleted", compare_id=compare_id)
            except Exception:
                # Still listed, so the next run retries it.
                logger.exception("compare_objects_delete_failed", compare_id=compare_id)
    except Exception:
        logger.exception("compare_objects_list_failed", bucket=bucket)


def sweep_remote_compare_objects(deadline: float, now: datetime | None = None):
    """Remove expired comparisons' objects from the private bucket, including
    those whose local folder lives on another replica or was lost with it,
    then those an earlier release left under compare/ in the upload bucket.
    Each bucket gets its share of the time left before ``deadline``
    (``time.monotonic()``), so the upload bucket is swept on every run; what
    is left is listed again by the next run."""
    now = now or datetime.now(UTC)
    private_deadline = time.monotonic() + (deadline - time.monotonic()) / 2
    try:
        client, bucket = _client_and_bucket()
    except Exception:
        logger.exception("compare_bucket_unavailable")
    else:
        _sweep_bucket(client, bucket, private_deadline, now)
    upload_bucket = str(getattr(settings, "UPLOAD_BUCKET_NAME", "") or "").strip()
    if not upload_bucket:
        return
    client = get_storage_client()
    try:
        if not client.bucket_exists(upload_bucket):
            return
    except Exception:
        logger.exception("compare_objects_list_failed", bucket=upload_bucket)
        return
    _sweep_bucket(client, upload_bucket, deadline, now)
