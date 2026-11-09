"""Shared cleanup for comparison scratch files and remote pages."""

import shutil
from uuid import UUID

from tfc.utils import storage


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
