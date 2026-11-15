"""Compatibility entry points for comparison cleanup.

The shared private compare store owns both local and remote cleanup. Keep the
older entry points for callers, but never use the removed public-upload helpers.
"""

import threading
from uuid import UUID

import structlog

from model_hub.utils import compare_storage

logger = structlog.get_logger(__name__)


def cleanup_compare_folder(compare_id: str | UUID) -> None:
    """Keep the local discovery marker until remote deletion succeeds."""
    compare_storage.remove_compare(compare_id)


def _cleanup_in_background(compare_id: str) -> None:
    try:
        cleanup_compare_folder(compare_id)
    except Exception:
        logger.exception("Error deleting compare folder", folder=compare_id)


def start_compare_cleanup(compare_id: str | UUID) -> threading.Thread:
    """Remove one comparison in the process that owns its local marker."""
    thread = threading.Thread(
        target=_cleanup_in_background,
        args=(str(compare_id),),
        name="compare-cleanup",
        daemon=True,
    )
    thread.start()
    return thread


def sweep_local_compare_root() -> None:
    compare_storage.sweep_local_compare_root()


def sweep_remote_compare_prefixes(*, deadline: float) -> bool:
    """Sweep private comparisons and legacy upload prefixes within the budget."""
    return compare_storage.sweep_remote_compare_objects(deadline=deadline)


def start_local_sweeper() -> None:
    compare_storage.start_local_sweeper()
