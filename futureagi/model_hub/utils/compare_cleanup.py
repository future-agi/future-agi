"""Cleanup for comparison scratch: local markers and remote pages.

A comparison keeps its ``metadata.json`` marker under the compare root of the
API process that serves it, and its pages in object storage under
``compare/<id>/``. In the distributed install that root is local to each API
container (the Helm chart mounts it as a per-pod emptyDir), so the worker that
runs the schedule cannot see it. Cleanup therefore runs where each file lives:

* pages: any process lists ``compare/<id>/`` in object storage and removes the
  prefixes whose newest page has expired, so pages whose marker was lost with
  its container are removed too;
* markers: the process that owns the root sweeps it. That is the scheduled
  activity when the worker shares the root, and every API process that writes
  markers (see ``start_local_sweeper``).
"""

import functools
import os
import shutil
import stat
import threading
import time
from datetime import timedelta
from uuid import UUID

import structlog
from django.utils import timezone

from tfc.utils import storage

logger = structlog.get_logger(__name__)

COMPARE_MAX_AGE_SECONDS = 2 * 60 * 60
# How often an API process sweeps the markers it wrote.
LOCAL_SWEEP_INTERVAL_SECONDS = 10 * 60
REMOTE_PREFIX = "compare/"


def _ignore_missing_path(function, path, exc_info):
    # rmtree's callback also handles children removed by a concurrent cleanup.
    error = exc_info[1]
    if not isinstance(error, FileNotFoundError):
        raise error


def cleanup_compare_folder(compare_id: str | UUID) -> None:
    """Keep the local discovery marker until remote deletion succeeds."""
    storage.delete_compare_folder(compare_id)
    shutil.rmtree(
        storage.get_compare_local_dir(compare_id), onerror=_ignore_missing_path
    )


def _cleanup_in_background(compare_id: str) -> None:
    try:
        cleanup_compare_folder(compare_id)
    except Exception:
        # Thread boundary, best effort: the marker stays, so the sweeps retry.
        logger.exception("Error deleting compare folder", folder=compare_id)


def start_compare_cleanup(compare_id: str | UUID) -> threading.Thread:
    """Remove one comparison in this process, without blocking the caller.

    It runs here, not in a worker, because only this process can see the
    comparison's marker in the distributed install.
    """
    thread = threading.Thread(
        target=_cleanup_in_background,
        args=(str(compare_id),),
        name="compare-cleanup",
        daemon=True,
    )
    thread.start()
    return thread


def sweep_local_compare_root() -> None:
    """Remove expired comparisons whose marker is under this process's root."""
    compare_root = storage.get_compare_local_root()
    try:
        folders = os.listdir(compare_root)
    except FileNotFoundError:
        return
    except OSError:
        logger.error(
            "Error deleting unused compare folder",
            compare_root=compare_root,
            exc_info=True,
        )
        return

    for folder in folders:
        folder_path = os.path.join(compare_root, folder)
        try:
            try:
                folder_stat = os.stat(folder_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(folder_stat.st_mode):
                continue
            if (time.time() - folder_stat.st_ctime) > COMPARE_MAX_AGE_SECONDS:
                cleanup_compare_folder(folder)
                logger.info("Deleted compare folder", folder_path=folder_path)
        except Exception:
            # Keep failed markers for the next sweep; continue other folders.
            logger.error("Error deleting compare folder", folder=folder, exc_info=True)


def _expired_remote_compare_ids() -> list[str]:
    """Ids under compare/ whose newest page is older than the age limit.

    Oldest first. A page with no modification time counts as new, so its
    comparison is kept.
    """
    client = storage.get_storage_client()
    bucket = storage.UPLOAD_BUCKET_NAME
    if not client.bucket_exists(bucket):
        return []
    now = timezone.now()
    newest = {}
    for obj in client.list_objects(bucket, prefix=REMOTE_PREFIX, recursive=True):
        compare_id, _, page = obj.object_name[len(REMOTE_PREFIX) :].partition("/")
        if not compare_id or not page:
            continue
        modified = getattr(obj, "last_modified", None) or now
        newest[compare_id] = max(newest.get(compare_id, modified), modified)
    cutoff = now - timedelta(seconds=COMPARE_MAX_AGE_SECONDS)
    expired = [(modified, cid) for cid, modified in newest.items() if modified < cutoff]
    return [cid for _, cid in sorted(expired)]


def sweep_remote_compare_prefixes(*, deadline: float) -> None:
    """Remove expired comparisons' pages from object storage.

    Needs no local marker, so it reaches pages written by any API process.
    Stops starting deletions at ``deadline`` (``time.monotonic()``); what is
    left is still listed by the next run.
    """
    try:
        expired = _expired_remote_compare_ids()
    except Exception:
        # Call into object storage: retried by the next scheduled run.
        logger.error("Error listing compare pages", exc_info=True)
        return

    for position, compare_id in enumerate(expired):
        if time.monotonic() >= deadline:
            logger.warning(
                "Deferred expired compare pages to the next run",
                remaining=len(expired) - position,
            )
            return
        try:
            storage.delete_compare_folder(compare_id)
            logger.info("Deleted compare pages", compare_id=compare_id)
        except Exception:
            # Pages stay listed, so the next run retries; continue the others.
            logger.error(
                "Error deleting compare pages", compare_id=compare_id, exc_info=True
            )


class _LocalSweeper:
    """One thread per process that sweeps the compare root periodically."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def ensure_running(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._run, name="compare-local-sweep", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join()

    def _run(self) -> None:
        while not self._stop.wait(LOCAL_SWEEP_INTERVAL_SECONDS):
            try:
                sweep_local_compare_root()
            except Exception:
                # Thread boundary: log and keep sweeping on the next interval.
                logger.exception("Error sweeping compare root")


@functools.cache
def _local_sweeper() -> _LocalSweeper:
    return _LocalSweeper()


def start_local_sweeper() -> None:
    """Keep this process sweeping the compare markers it writes.

    Called where the API writes a marker. In the distributed install no other
    process can see this root, so the scheduled activity cannot remove them.
    """
    _local_sweeper().ensure_running()
