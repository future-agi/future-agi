"""Every process that builds comparisons sweeps its own local folders."""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from model_hub.tests.test_dataset_compare_storage import _FakeBucketClient
from model_hub.utils import compare_storage
from tfc.utils import storage_client


@pytest.fixture
def storage(monkeypatch, tmp_path, settings):
    settings.UPLOAD_BUCKET_NAME = "test-upload"
    settings.COMPARE_BUCKET_NAME = "test-upload-compare"
    monkeypatch.setenv("COMPARE_DATASET_LOCAL_ROOT", str(tmp_path / "compare"))
    client = _FakeBucketClient({})
    monkeypatch.setattr(storage_client, "_client", client)
    monkeypatch.setattr(storage_client, "STORAGE_BACKEND", "minio")
    return client


def _local_compare(compare_id):
    folder = Path(compare_storage.get_compare_local_dir(compare_id))
    folder.mkdir(parents=True)
    (folder / "metadata.json").write_text("{}", encoding="utf-8")
    return folder


def _wait_for(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


def test_a_process_sweeps_its_own_expired_comparisons(monkeypatch, storage):
    old = datetime.now(UTC) - timedelta(hours=3)
    expired_id, fresh_id = str(uuid.uuid4()), str(uuid.uuid4())
    storage.objects.update(
        {
            f"test-upload-compare/compare/{expired_id}/page_1.json": old,
            f"test-upload-compare/compare/{fresh_id}/page_1.json": old,
        }
    )
    expired, fresh = _local_compare(expired_id), _local_compare(fresh_id)
    real_getctime = os.path.getctime
    monkeypatch.setattr(
        compare_storage.os.path,
        "getctime",
        lambda path: (
            time.time() - 3 * 60 * 60 if Path(path) == expired else real_getctime(path)
        ),
    )
    monkeypatch.setattr(compare_storage, "LOCAL_SWEEP_INTERVAL_SECONDS", 0.01)
    sweeper = compare_storage._LocalSweeper()
    sweeper.ensure_running()
    try:
        assert _wait_for(lambda: not expired.exists())
    finally:
        sweeper.stop()
    assert fresh.exists()
    assert list(storage.objects) == [
        f"test-upload-compare/compare/{fresh_id}/page_1.json"
    ]


def test_the_sweeper_starts_once_per_process(monkeypatch):
    monkeypatch.setattr(compare_storage, "LOCAL_SWEEP_INTERVAL_SECONDS", 3600)
    sweeper = compare_storage._LocalSweeper()
    sweeper.ensure_running()
    first = sweeper._thread
    sweeper.ensure_running()
    try:
        assert sweeper._thread is first and first.is_alive() and first.daemon
    finally:
        sweeper.stop()
    assert not first.is_alive()


def test_the_sweeper_survives_an_unexpected_error(monkeypatch):
    calls = []

    def sweep():
        calls.append(None)
        if len(calls) == 1:
            raise RuntimeError("unexpected")

    monkeypatch.setattr(compare_storage, "sweep_local_compare_root", sweep)
    monkeypatch.setattr(compare_storage, "LOCAL_SWEEP_INTERVAL_SECONDS", 0.01)
    sweeper = compare_storage._LocalSweeper()
    sweeper.ensure_running()
    try:
        assert _wait_for(lambda: len(calls) >= 2)
    finally:
        sweeper.stop()


def test_an_unreadable_local_root_does_not_stop_the_remote_sweep(monkeypatch, storage):
    from model_hub.tasks.develop_dataset import delete_unused_compare_folder

    old = datetime.now(UTC) - timedelta(hours=3)
    remote_id = str(uuid.uuid4())
    storage.objects[f"test-upload-compare/compare/{remote_id}/page_1.json"] = old
    real_listdir = os.listdir
    root = compare_storage.get_compare_local_root()

    def listdir(path):
        if os.fspath(path) == root:
            raise PermissionError("denied")
        return real_listdir(path)

    monkeypatch.setattr(compare_storage.os, "listdir", listdir)
    delete_unused_compare_folder()
    assert storage.objects == {}


def test_an_unreadable_local_root_is_logged(monkeypatch, storage):
    from structlog.testing import capture_logs

    real_listdir = os.listdir
    root = compare_storage.get_compare_local_root()

    def listdir(path):
        if os.fspath(path) == root:
            raise PermissionError("denied")
        return real_listdir(path)

    monkeypatch.setattr(compare_storage.os, "listdir", listdir)
    with capture_logs() as logs:
        compare_storage.sweep_local_compare_root()

    unreadable = [log for log in logs if log["event"] == "compare_root_unreadable"]
    assert len(unreadable) == 1
    assert unreadable[0]["log_level"] == "error"
    assert unreadable[0]["root"] == root
    assert unreadable[0]["exc_info"] is True


def test_building_a_comparison_starts_the_sweeper(monkeypatch):
    from model_hub.views import develop_dataset

    started = []
    monkeypatch.setattr(
        develop_dataset,
        "start_local_sweeper",
        lambda: started.append(True),
        raising=False,
    )
    monkeypatch.setattr(
        develop_dataset, "get_compare_local_dir", lambda compare_id: "/nonexistent"
    )

    def stop(*args, **kwargs):
        raise RuntimeError("stop after the folder is made")

    monkeypatch.setattr(develop_dataset.os, "makedirs", lambda *a, **k: None)
    monkeypatch.setattr("builtins.open", stop)
    develop_dataset._prepare_compare_dataset_impl(
        str(uuid.uuid4()),
        set(),
        "base",
        {},
        [],
        {},
        None,
        set(),
        str(uuid.uuid4()),
        [],
        {},
        {},
    )
    assert started == [True]
