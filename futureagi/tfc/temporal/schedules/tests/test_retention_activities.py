import builtins
from types import ModuleType
from unittest.mock import MagicMock

import pytest

from tfc.temporal.schedules import retention

_RETENTION_MODULE = "ee.usage.tasks.retention"


@pytest.mark.parametrize(
    ("activity", "event"),
    [
        (
            retention.soft_delete_expired_data_activity,
            "soft_delete_activity_skipped",
        ),
        (
            retention.hard_delete_expired_data_activity,
            "hard_delete_activity_skipped",
        ),
    ],
)
def test_retention_activity_skips_when_dependency_is_unavailable(
    monkeypatch, activity, event
):
    real_import = builtins.__import__

    def import_without_retention(name, *args, **kwargs):
        if name == _RETENTION_MODULE:
            raise ImportError("retention dependency unavailable")
        return real_import(name, *args, **kwargs)

    logger = MagicMock()
    monkeypatch.setattr(builtins, "__import__", import_without_retention)
    monkeypatch.setattr(retention, "logger", logger)

    assert activity._original_func() == {}
    logger.info.assert_called_once_with(
        event,
        dependency=_RETENTION_MODULE,
        reason="retention_dependency_unavailable",
    )


@pytest.mark.parametrize(
    ("activity", "callable_name", "result"),
    [
        (
            retention.soft_delete_expired_data_activity,
            "soft_delete_expired_data",
            {"org-1": {"spans": 2, "traces": 1}},
        ),
        (
            retention.hard_delete_expired_data_activity,
            "hard_delete_expired_data",
            {"spans": 3, "traces": 1},
        ),
    ],
)
def test_retention_activity_invokes_available_dependency(
    monkeypatch, activity, callable_name, result
):
    real_import = builtins.__import__
    retention_callable = MagicMock(return_value=result)
    retention_module = ModuleType(_RETENTION_MODULE)
    setattr(retention_module, callable_name, retention_callable)

    def import_with_retention(name, *args, **kwargs):
        if name == _RETENTION_MODULE:
            return retention_module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_retention)

    assert activity._original_func() == result
    retention_callable.assert_called_once_with()


def test_hard_delete_activity_runs_trajectory_gc_after_retention(monkeypatch):
    from tracer.services.clickhouse.v2 import trajectory_lifecycle

    real_import = builtins.__import__
    order = []
    retention_module = ModuleType(_RETENTION_MODULE)
    retention_module.hard_delete_expired_data = lambda: (
        order.append("spans") or {"traces": 1}
    )

    def import_with_retention(name, *args, **kwargs):
        if name == _RETENTION_MODULE:
            return retention_module
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_with_retention)
    monkeypatch.setattr(
        trajectory_lifecycle, "trajectory_items_gc", lambda: order.append("gc") or {}
    )

    assert retention.hard_delete_expired_data_activity._original_func() == {"traces": 1}
    assert order == ["spans", "gc"]


def test_trajectory_gc_failure_does_not_fail_hard_delete(monkeypatch):
    from tracer.services.clickhouse.v2 import trajectory_lifecycle

    real_import = builtins.__import__
    retention_module = ModuleType(_RETENTION_MODULE)
    retention_module.hard_delete_expired_data = lambda: {"traces": 2}

    def import_with_retention(name, *args, **kwargs):
        if name == _RETENTION_MODULE:
            return retention_module
        return real_import(name, *args, **kwargs)

    def boom():
        raise RuntimeError("clickhouse down")

    monkeypatch.setattr(builtins, "__import__", import_with_retention)
    monkeypatch.setattr(trajectory_lifecycle, "trajectory_items_gc", boom)
    assert retention.hard_delete_expired_data_activity._original_func() == {"traces": 2}


def test_trajectory_gc_schedule_registered():
    from temporalio.client import ScheduleOverlapPolicy

    import tracer.tasks  # noqa: F401  registers the drop-in activities
    from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY
    from tfc.temporal.schedules import ALL_SCHEDULES, TRACER_SCHEDULES

    (schedule,) = [s for s in ALL_SCHEDULES if s.schedule_id == "trajectory-items-gc"]
    assert schedule in TRACER_SCHEDULES  # not EE-gated
    assert schedule.activity_name == "tracer.trajectory_items_gc"
    assert schedule.queue == "tasks_l"
    assert schedule.interval_seconds == 86400
    assert schedule.overlap_policy == ScheduleOverlapPolicy.SKIP
    meta = _ACTIVITY_REGISTRY["tracer.trajectory_items_gc"]
    assert meta["queue"] == "tasks_l" and meta["time_limit"] == 4 * 3600
