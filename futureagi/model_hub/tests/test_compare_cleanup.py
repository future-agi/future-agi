"""Comparison cleanup reaches markers and pages wherever they were written."""

import errno
import os
import shutil
import time as real_time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from django.utils import timezone

from model_hub.tasks import develop_dataset as tasks
from model_hub.utils import compare_cleanup
from tfc.temporal.background_tasks.activities import delete_compare_folder_activity
from tfc.utils import storage

FIRST_ID = "6f1c2a52-6d0b-4a0e-9f5e-0c7b1a2d3e01"
SECOND_ID = "6f1c2a52-6d0b-4a0e-9f5e-0c7b1a2d3e02"
SWEEP_LISTING = call(storage.UPLOAD_BUCKET_NAME, prefix="compare/", recursive=True)


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path, settings):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COMPARE_DATASET_LOCAL_ROOT", raising=False)
    settings.BASE_DIR = tmp_path
    client = Mock()
    client.list_objects.return_value = [
        SimpleNamespace(object_name="compare/test/page.json")
    ]
    monkeypatch.setattr(storage, "get_storage_client", lambda: client)
    monkeypatch.setattr(compare_cleanup, "logger", Mock(), raising=False)
    return client


class RemotePages:
    """A bucket whose compare/<id>/ pages carry modification times."""

    def __init__(self, client, pages):
        self.pages = dict(pages)
        self.fail_ids = set()
        client.list_objects.side_effect = self.list_objects
        client.remove_object.side_effect = self.remove_object

    def list_objects(self, bucket, *, prefix, recursive):
        return [
            SimpleNamespace(object_name=name, last_modified=modified)
            for name, modified in sorted(self.pages.items())
            if name.startswith(prefix)
        ]

    def remove_object(self, bucket, name):
        if any(name.startswith(f"compare/{cid}/") for cid in self.fail_ids):
            raise PermissionError("remote denied")
        self.pages.pop(name)

    def ids(self):
        return {name.split("/")[1] for name in self.pages}


def aged(seconds):
    return timezone.now() - timedelta(seconds=seconds)


def make_compare(compare_id):
    folder = Path(storage.get_compare_local_dir(compare_id))
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text("{}", encoding="utf-8")
    return folder


def frozen_clock(now):
    return SimpleNamespace(
        time=lambda: now, monotonic=real_time.monotonic, sleep=real_time.sleep
    )


def expire_folders(monkeypatch, *folders):
    # ctime cannot be set portably; freeze only the sweep's clock.
    now = max(folder.stat().st_ctime for folder in folders) + 7201
    monkeypatch.setattr(compare_cleanup, "time", frozen_clock(now))


def logged_errors():
    return compare_cleanup.logger.error.call_args_list


@pytest.mark.parametrize(
    "configured", [False, True], ids=["default-root", "configured-root"]
)
def test_scheduled_cleanup_uses_writer_root(
    monkeypatch, tmp_path, isolated_storage, configured
):
    if configured:
        monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "scratch"))
    expired = make_compare("expired")
    fresh = make_compare("fresh")
    boundary = make_compare("boundary")
    ordinary_file = expired.parent / "keep.txt"
    ordinary_file.write_text("keep", encoding="utf-8")
    target = tmp_path / "outside"
    target.mkdir()
    link = expired.parent / "link"
    link.symlink_to(target, target_is_directory=True)
    now = expired.stat().st_ctime + 7201
    original_stat = os.stat

    def folder_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if Path(path) in (fresh, boundary):
            age = 0 if Path(path) == fresh else 7200
            return SimpleNamespace(st_mode=result.st_mode, st_ctime=now - age)
        return result

    monkeypatch.setattr(compare_cleanup.os, "stat", folder_stat)
    monkeypatch.setattr(compare_cleanup, "time", frozen_clock(now))
    tasks.delete_unused_compare_folder()

    assert not expired.exists()
    assert fresh.exists() and boundary.exists()
    assert ordinary_file.exists() and target.exists() and link.is_symlink()
    assert isolated_storage.list_objects.call_args_list == [
        call(storage.UPLOAD_BUCKET_NAME, prefix="compare/expired/", recursive=True),
        SWEEP_LISTING,
    ]
    isolated_storage.remove_object.assert_called_once_with(
        storage.UPLOAD_BUCKET_NAME, "compare/test/page.json"
    )
    assert logged_errors() == []


@pytest.mark.parametrize("configured", [False, True])
def test_missing_root_is_quiet(monkeypatch, tmp_path, isolated_storage, configured):
    if configured:
        monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "missing"))
    tasks.delete_unused_compare_folder()
    assert logged_errors() == []
    # Only the remote sweep lists; its page has no age, so it is kept.
    assert isolated_storage.list_objects.call_args_list == [SWEEP_LISTING]
    isolated_storage.remove_object.assert_not_called()
    assert not Path(storage.get_compare_local_root()).exists()


def test_root_permission_error_is_logged(monkeypatch):
    monkeypatch.setattr(
        compare_cleanup.os, "listdir", Mock(side_effect=PermissionError("denied"))
    )
    tasks.delete_unused_compare_folder()
    compare_cleanup.logger.error.assert_called_once()
    assert compare_cleanup.logger.error.call_args.kwargs["exc_info"] is True


def test_child_disappears_after_listing(monkeypatch, tmp_path, isolated_storage):
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    missing = make_compare("missing")
    expired = make_compare("expired")
    expire_folders(monkeypatch, missing, expired)
    original_listdir = os.listdir

    def list_then_remove(path):
        names = original_listdir(path)
        shutil.rmtree(missing)
        return names

    monkeypatch.setattr(compare_cleanup.os, "listdir", list_then_remove)
    tasks.delete_unused_compare_folder()
    assert not expired.exists()
    assert isolated_storage.list_objects.call_args_list == [
        call(storage.UPLOAD_BUCKET_NAME, prefix="compare/expired/", recursive=True),
        SWEEP_LISTING,
    ]
    assert logged_errors() == []


def test_scheduled_remote_failure_keeps_marker_and_continues(
    monkeypatch, tmp_path, isolated_storage
):
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    failed = make_compare("failed")
    expired = make_compare("expired")
    expire_folders(monkeypatch, failed, expired)
    monkeypatch.setattr(
        compare_cleanup.os, "listdir", lambda path: ["failed", "expired"]
    )

    def list_objects(bucket, *, prefix, recursive):
        if prefix == "compare/failed/":
            raise PermissionError("remote denied")
        return [SimpleNamespace(object_name=f"{prefix}page.json")]

    isolated_storage.list_objects.side_effect = list_objects
    tasks.delete_unused_compare_folder()
    assert (failed / "metadata.json").exists()
    assert not expired.exists()
    compare_cleanup.logger.error.assert_called_once()

    isolated_storage.list_objects.side_effect = None
    tasks.delete_unused_compare_folder()
    assert not failed.exists()


def test_worker_without_root_removes_expired_pages(isolated_storage):
    """Split topology: the API wrote the markers, the worker sees none of them."""
    remote = RemotePages(
        isolated_storage,
        {
            f"compare/{FIRST_ID}/page_1.json": aged(7300),
            f"compare/{FIRST_ID}/page_2.json": aged(7250),
            f"compare/{SECOND_ID}/page_1.json": aged(60),
        },
    )
    assert not Path(storage.get_compare_local_root()).exists()

    tasks.delete_unused_compare_folder()

    assert remote.ids() == {SECOND_ID}
    assert logged_errors() == []


def test_remote_age_is_the_newest_page(isolated_storage):
    # Pages are written while the comparison is prepared; the last one counts.
    remote = RemotePages(
        isolated_storage,
        {
            f"compare/{FIRST_ID}/page_1.json": aged(9000),
            f"compare/{FIRST_ID}/page_2.json": aged(7100),
            f"compare/{SECOND_ID}/page_1.json": aged(7300),
            "compare/page.json": aged(9000),
        },
    )

    tasks.delete_unused_compare_folder()

    assert remote.pages.keys() == {
        f"compare/{FIRST_ID}/page_1.json",
        f"compare/{FIRST_ID}/page_2.json",
        "compare/page.json",
    }


def test_remote_sweep_failure_is_retried_by_the_next_run(isolated_storage):
    remote = RemotePages(
        isolated_storage,
        {
            f"compare/{FIRST_ID}/page_1.json": aged(7400),
            f"compare/{SECOND_ID}/page_1.json": aged(7300),
        },
    )
    remote.fail_ids = {FIRST_ID}

    tasks.delete_unused_compare_folder()

    assert remote.ids() == {FIRST_ID}
    compare_cleanup.logger.error.assert_called_once()
    assert compare_cleanup.logger.error.call_args.kwargs["compare_id"] == FIRST_ID

    remote.fail_ids = set()
    tasks.delete_unused_compare_folder()
    assert remote.ids() == set()


def test_remote_listing_failure_is_logged(isolated_storage):
    isolated_storage.list_objects.side_effect = ConnectionError("unreachable")
    tasks.delete_unused_compare_folder()
    compare_cleanup.logger.error.assert_called_once()
    assert compare_cleanup.logger.error.call_args.kwargs["exc_info"] is True


def test_missing_bucket_is_quiet(isolated_storage):
    isolated_storage.bucket_exists.return_value = False
    tasks.delete_unused_compare_folder()
    isolated_storage.list_objects.assert_not_called()
    assert logged_errors() == []


def test_remote_sweep_stops_at_the_deadline(monkeypatch, isolated_storage):
    remote = RemotePages(
        isolated_storage,
        {
            f"compare/{FIRST_ID}/page_1.json": aged(9000),
            f"compare/{SECOND_ID}/page_1.json": aged(8000),
        },
    )
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(
        compare_cleanup, "time", SimpleNamespace(monotonic=lambda: clock.now)
    )
    original_delete = storage.delete_compare_folder

    def delete_then_run_out_of_time(compare_id):
        original_delete(compare_id)
        clock.now = 100.0

    monkeypatch.setattr(storage, "delete_compare_folder", delete_then_run_out_of_time)

    compare_cleanup.sweep_remote_compare_prefixes(deadline=50.0)
    # The oldest goes first; the rest waits for the next run.
    assert remote.ids() == {SECOND_ID}
    assert compare_cleanup.logger.warning.call_args.kwargs["remaining"] == 1

    compare_cleanup.sweep_remote_compare_prefixes(deadline=200.0)
    assert remote.ids() == set()


def test_api_process_sweeps_its_own_root(monkeypatch, tmp_path, isolated_storage):
    """The API's sweeper removes expired markers and their pages, keeps fresh ones."""
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    remote = RemotePages(
        isolated_storage,
        {
            f"compare/{FIRST_ID}/page_1.json": aged(60),
            f"compare/{SECOND_ID}/page_1.json": aged(60),
        },
    )
    expired = make_compare(FIRST_ID)
    fresh = make_compare(SECOND_ID)
    original_stat = os.stat

    def folder_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if Path(path) == expired:
            return SimpleNamespace(
                st_mode=result.st_mode, st_ctime=real_time.time() - 7201
            )
        return result

    monkeypatch.setattr(compare_cleanup.os, "stat", folder_stat)
    monkeypatch.setattr(compare_cleanup, "LOCAL_SWEEP_INTERVAL_SECONDS", 0.01)
    sweeper = compare_cleanup._LocalSweeper()
    sweeper.ensure_running()
    try:
        deadline = real_time.monotonic() + 10
        while expired.exists() and real_time.monotonic() < deadline:
            real_time.sleep(0.02)
    finally:
        sweeper.stop()

    assert not expired.exists()
    assert fresh.exists()
    assert remote.ids() == {SECOND_ID}
    assert logged_errors() == []


def test_api_sweeper_starts_once(monkeypatch):
    monkeypatch.setattr(compare_cleanup, "LOCAL_SWEEP_INTERVAL_SECONDS", 3600)
    sweeper = compare_cleanup._LocalSweeper()
    sweeper.ensure_running()
    first = sweeper._thread
    sweeper.ensure_running()
    try:
        assert sweeper._thread is first and first.is_alive() and first.daemon
    finally:
        sweeper.stop()
    assert not first.is_alive()


def test_api_sweeper_survives_an_unexpected_error(monkeypatch):
    calls = []

    def sweep():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(compare_cleanup, "sweep_local_compare_root", sweep)
    monkeypatch.setattr(compare_cleanup, "LOCAL_SWEEP_INTERVAL_SECONDS", 0.01)
    sweeper = compare_cleanup._LocalSweeper()
    sweeper.ensure_running()
    try:
        deadline = real_time.monotonic() + 10
        while len(calls) < 2 and real_time.monotonic() < deadline:
            real_time.sleep(0.02)
    finally:
        sweeper.stop()
    assert len(calls) >= 2
    compare_cleanup.logger.exception.assert_called_once()


@pytest.fixture(params=["activity", "api"])
def explicit_cleanup(request):
    if request.param == "activity":
        return delete_compare_folder_activity
    # What the API's DELETE runs in its background thread.
    return compare_cleanup.cleanup_compare_folder


def test_explicit_remote_failure_preserves_marker(explicit_cleanup, isolated_storage):
    folder = make_compare("retry")
    isolated_storage.remove_object.side_effect = PermissionError("remote denied")
    with pytest.raises(ValueError):
        explicit_cleanup("retry")
    assert (folder / "metadata.json").exists()
    isolated_storage.remove_object.side_effect = None
    explicit_cleanup("retry")
    assert not folder.exists()


def test_explicit_cleanup_is_repeatable(explicit_cleanup, isolated_storage):
    folder = make_compare("repeat")
    explicit_cleanup("repeat")
    explicit_cleanup("repeat")
    assert not folder.exists()
    assert isolated_storage.list_objects.call_count == 2


def test_concurrent_local_removal_is_quiet(explicit_cleanup, isolated_storage):
    folder = make_compare("race")
    isolated_storage.remove_object.side_effect = lambda *args: shutil.rmtree(folder)
    explicit_cleanup("race")
    assert not folder.exists()


def test_local_permission_error_is_not_suppressed(explicit_cleanup, monkeypatch):
    folder = make_compare("permission")
    original_open = os.open

    def denied_open(path, *args, **kwargs):
        if Path(path) == folder:
            raise PermissionError(errno.EACCES, "denied", str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied_open)
    with pytest.raises(PermissionError):
        explicit_cleanup("permission")
    assert folder.exists()


def test_concurrently_removed_child_does_not_leave_other_files(
    explicit_cleanup, monkeypatch
):
    folder = make_compare("child-race")
    (folder / "page.json").write_text("{}", encoding="utf-8")
    original_unlink = os.unlink
    removed = False

    def unlink_then_report_missing(path, *args, **kwargs):
        nonlocal removed
        original_unlink(path, *args, **kwargs)
        if not removed:
            removed = True
            raise FileNotFoundError(errno.ENOENT, "already removed", str(path))

    monkeypatch.setattr(os, "unlink", unlink_then_report_missing)
    explicit_cleanup("child-race")
    assert not folder.exists()


def test_background_cleanup_failure_keeps_marker(isolated_storage):
    folder = make_compare("background")
    isolated_storage.list_objects.side_effect = PermissionError("remote denied")
    compare_cleanup.start_compare_cleanup("background").join(10)
    assert (folder / "metadata.json").exists()
    compare_cleanup.logger.exception.assert_called_once()

    isolated_storage.list_objects.side_effect = None
    compare_cleanup.start_compare_cleanup("background").join(10)
    assert not folder.exists()
