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
