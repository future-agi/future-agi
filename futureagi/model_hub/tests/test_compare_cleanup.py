"""Comparison cleanup keeps local retry markers and shares the writer's root."""

import errno
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model_hub.tasks import develop_dataset as tasks
from model_hub.views.develop_dataset import GetCompareDatasetRow
from tfc.temporal.background_tasks.activities import delete_compare_folder_activity
from tfc.utils import storage


@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path, settings):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("COMPARE_DATASET_LOCAL_ROOT", raising=False)
    settings.BASE_DIR = tmp_path
    client = Mock()
    client.list_objects.return_value = [SimpleNamespace(object_name="compare/test/page.json")]
    monkeypatch.setattr(storage, "get_storage_client", lambda: client)
    monkeypatch.setattr(tasks, "logger", Mock())
    return client


def make_compare(compare_id):
    folder = Path(storage.get_compare_local_dir(compare_id))
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text("{}", encoding="utf-8")
    return folder


def expire_folders(monkeypatch, *folders):
    # ctime cannot be set portably; freeze only the scheduler's clock.
    now = max(folder.stat().st_ctime for folder in folders) + 7201
    monkeypatch.setattr(tasks, "time", SimpleNamespace(time=lambda: now))


@pytest.mark.parametrize("configured", [False, True], ids=["default-root", "configured-root"])
def test_scheduled_cleanup_uses_writer_root(monkeypatch, tmp_path, isolated_storage, configured):
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

    monkeypatch.setattr(tasks.os, "stat", folder_stat)
    monkeypatch.setattr(tasks, "time", SimpleNamespace(time=lambda: now))
    tasks.delete_unused_compare_folder()

    assert not expired.exists()
    assert fresh.exists() and boundary.exists()
    assert ordinary_file.exists() and target.exists() and link.is_symlink()
    isolated_storage.list_objects.assert_called_once_with(
        storage.UPLOAD_BUCKET_NAME, prefix="compare/expired/", recursive=True
    )
    tasks.logger.error.assert_not_called()


@pytest.mark.parametrize("configured", [False, True])
def test_missing_root_is_quiet(monkeypatch, tmp_path, isolated_storage, configured):
    if configured:
        monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "missing"))
    tasks.delete_unused_compare_folder()
    tasks.logger.error.assert_not_called()
    isolated_storage.list_objects.assert_not_called()
    assert not Path(storage.get_compare_local_root()).exists()


def test_root_permission_error_is_logged(monkeypatch):
    monkeypatch.setattr(tasks.os, "listdir", Mock(side_effect=PermissionError("denied")))
    tasks.delete_unused_compare_folder()
    tasks.logger.error.assert_called_once()
    assert "denied" in tasks.logger.error.call_args.args[0]


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

    monkeypatch.setattr(tasks.os, "listdir", list_then_remove)
    tasks.delete_unused_compare_folder()
    assert not expired.exists()
    isolated_storage.list_objects.assert_called_once()
    tasks.logger.error.assert_not_called()


def test_scheduled_remote_failure_keeps_marker_and_continues(monkeypatch, tmp_path, isolated_storage):
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    failed = make_compare("failed")
    expired = make_compare("expired")
    expire_folders(monkeypatch, failed, expired)
    monkeypatch.setattr(tasks.os, "listdir", lambda path: ["failed", "expired"])

    def list_objects(bucket, *, prefix, recursive):
        if prefix == "compare/failed/":
            raise PermissionError("remote denied")
        return [SimpleNamespace(object_name=f"{prefix}page.json")]

    isolated_storage.list_objects.side_effect = list_objects
    tasks.delete_unused_compare_folder()
    assert (failed / "metadata.json").exists()
    assert not expired.exists()
    tasks.logger.error.assert_called_once()

    isolated_storage.list_objects.side_effect = None
    tasks.delete_unused_compare_folder()
    assert not failed.exists()


@pytest.fixture(params=["activity", "fallback"])
def explicit_cleanup(request):
    if request.param == "activity":
        return delete_compare_folder_activity
    return GetCompareDatasetRow().thread_delete


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


def test_concurrently_removed_child_does_not_leave_other_files(explicit_cleanup, monkeypatch):
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
