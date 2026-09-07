from datetime import UTC, datetime, timedelta
from threading import Event

import pytest

from tracer.services.clickhouse.v2.property_catalog.controller_health import (
    ControllerHealth,
)


def test_workspace_failure_is_degraded_not_a_dead_controller() -> None:
    health = ControllerHealth(lambda **_snapshot: None)
    health.progress(
        "idle",
        timeout_seconds=120,
        ready=True,
        detail={"failed_count": 1, "processed_count": 10},
    )
    result = health.snapshot()
    assert result["live"] is True
    assert result["ready"] is True
    assert result["healthy"] is False
    assert result["detail"]["failed_count"] == 1


def test_heartbeat_cannot_hide_a_stuck_operation() -> None:
    tick = [100.0]
    wall = [datetime(2026, 9, 6, tzinfo=UTC)]
    health = ControllerHealth(
        lambda **_snapshot: None,
        monotonic=lambda: tick[0],
        utc_now=lambda: wall[0],
    )
    health.progress("reconciling", timeout_seconds=20, ready=True)
    progress_at = health.snapshot()["progress_at"]
    tick[0] += 21
    wall[0] += timedelta(seconds=21)
    result = health.snapshot()
    assert result["observed_at"] > progress_at
    assert result["progress_at"] == progress_at
    assert result["live"] is False
    assert result["ready"] is False
    assert result["healthy"] is False


def test_each_workspace_gets_its_own_bounded_progress_deadline() -> None:
    tick = [100.0]
    health = ControllerHealth(lambda **_snapshot: None, monotonic=lambda: tick[0])
    health.progress("reconciling", timeout_seconds=20)
    tick[0] += 19
    health.progress("reconciling", timeout_seconds=20)
    tick[0] += 19
    assert health.snapshot()["live"] is True
    tick[0] += 2
    assert health.snapshot()["live"] is False


def test_dependency_retry_is_live_but_not_ready() -> None:
    health = ControllerHealth(lambda **_snapshot: None)
    health.progress(
        "retrying",
        timeout_seconds=120,
        ready=False,
        detail={"cycle_error": "database unavailable"},
    )
    assert health.snapshot()["live"] is True
    assert health.snapshot()["ready"] is False
    health.progress("idle", timeout_seconds=120, ready=True, detail={})
    assert health.snapshot()["healthy"] is True


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_unbounded_or_invalid_health_deadlines_are_rejected(timeout: float) -> None:
    health = ControllerHealth(lambda **_snapshot: None)
    with pytest.raises(ValueError, match="deadline"):
        health.progress("reconciling", timeout_seconds=timeout)


def test_health_is_published_during_work_and_shutdown_clears_readiness() -> None:
    snapshots = []
    heartbeat = Event()

    def publish(**snapshot):
        snapshots.append(snapshot)
        if len(snapshots) > 1:
            heartbeat.set()

    with ControllerHealth(publish, interval_seconds=0.01) as health:
        health.progress("reconciling", timeout_seconds=120, ready=True)
        assert heartbeat.wait(timeout=2)
        assert snapshots[-1]["live"] is True
    assert snapshots[-1]["live"] is False
    assert snapshots[-1]["ready"] is False


def test_publication_failure_is_not_silently_ignored() -> None:
    failure = Event()
    calls = []

    def publish(**snapshot):
        calls.append(snapshot)
        if len(calls) == 2:
            failure.set()
            raise OSError("read-only filesystem")

    health = ControllerHealth(publish, interval_seconds=0.01)
    with health:
        assert failure.wait(timeout=2)
        # Join the failed writer before checking the captured exception.
        health._thread.join(timeout=2)
        assert health.snapshot()["live"] is False
        with pytest.raises(RuntimeError, match="publisher failed"):
            health.progress("idle", timeout_seconds=120)
