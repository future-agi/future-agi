"""The telemetry buffer is used only when it is a private directory.

Its default location is under the shared temp directory. In the default
install that directory is shared with the unprivileged code-eval sandbox,
which could create the buffer directory before the API does and then read the
buffered windows or plant windows for the sender to sign and send.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from tfc.deployment_telemetry import buffer

pytestmark = pytest.mark.unit

WINDOW = (datetime(2026, 6, 7, 6, tzinfo=UTC), datetime(2026, 6, 7, 12, tzinfo=UTC))


@pytest.fixture
def buffer_dir(monkeypatch, tmp_path):
    path = tmp_path / "deployment-telemetry"
    monkeypatch.setenv("FUTURE_AGI_TELEMETRY_BUFFER_DIR", str(path))
    return path


@pytest.fixture
def owned_by_someone_else(monkeypatch):
    real = os.geteuid()
    monkeypatch.setattr(buffer.os, "geteuid", lambda: real + 1)


def test_a_private_directory_is_created_and_used(buffer_dir):
    stored = buffer.store_window(*WINDOW, {"instance_id": "x"})

    assert stored.parent == buffer_dir
    assert buffer_dir.stat().st_mode & 0o777 == 0o700
    assert buffer.pending_windows() == [stored]


def test_windows_are_isolated_by_instance_id(buffer_dir):
    first_instance = uuid4()
    second_instance = uuid4()
    first = buffer.store_window(*WINDOW, {"instance_id": str(first_instance)})
    second = buffer.store_window(*WINDOW, {"instance_id": str(second_instance)})

    assert first != second
    assert buffer.pending_windows(instance_id=str(first_instance)) == [first]
    assert buffer.pending_windows(instance_id=str(second_instance)) == [second]


def test_legacy_window_for_same_instance_is_migrated(buffer_dir):
    instance_id = uuid4()
    payload = {"instance_id": str(instance_id), "active_users_count": 1}
    legacy = buffer_dir / buffer._window_filename(*WINDOW)
    buffer_dir.mkdir(parents=True)
    legacy.write_text(json.dumps(payload), encoding="utf-8")

    scoped = buffer.store_window(*WINDOW, payload)

    assert scoped.exists()
    assert not legacy.exists()


def test_a_directory_someone_else_owns_is_never_read(buffer_dir, owned_by_someone_else):
    buffer_dir.mkdir()
    planted = buffer_dir / "planted.json"
    planted.write_text('{"instance_id": "x"}', encoding="utf-8")

    assert buffer.pending_windows() == []
    with pytest.raises(PermissionError):
        buffer.store_window(*WINDOW, {"instance_id": "x"})
    assert (
        buffer.prune_expired_windows(now=datetime.now(UTC) + timedelta(days=365)) == 0
    )
    assert planted.exists()


def test_a_symlink_is_not_trusted(buffer_dir, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "planted.json").write_text("{}", encoding="utf-8")
    buffer_dir.symlink_to(target, target_is_directory=True)

    assert buffer.pending_windows() == []
    with pytest.raises(PermissionError):
        buffer.store_window(*WINDOW, {"instance_id": "x"})
