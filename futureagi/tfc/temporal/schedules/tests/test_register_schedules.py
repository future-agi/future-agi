"""
Tests for the schedule manager's contract with the activity registry.

The fix: ``a_register_schedules`` must call ``_import_temporal_activity_modules``
before iterating ScheduleConfig entries, otherwise ``_ACTIVITY_REGISTRY`` is
empty when ``_build_schedule_for_config`` runs and every schedule falls back
to ``DEFAULT_RETRY_POLICY`` regardless of decorator-declared max_retries.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY, temporal_activity
from tfc.temporal.drop_in.workflow import TaskRunnerInput
from tfc.temporal.schedules.config import ScheduleConfig
from tfc.temporal.schedules.manager import (
    _build_schedule_for_config,
    a_register_schedules,
)


@pytest.fixture(autouse=True)
def _isolate_registry():
    snapshot = dict(_ACTIVITY_REGISTRY)
    try:
        yield
    finally:
        _ACTIVITY_REGISTRY.clear()
        _ACTIVITY_REGISTRY.update(snapshot)


class TestRegisterSchedulesPopulatesActivityRegistry:
    """Order-of-operations: imports must run BEFORE schedules are built."""

    @pytest.mark.asyncio
    async def test_register_schedules_calls_activity_module_import(self):
        """Confirms the bugfix call site: a_register_schedules invokes
        _import_temporal_activity_modules before iterating configs."""
        client = MagicMock()
        client.list_schedules = AsyncMock(return_value=_AsyncIterMock([]))
        client.get_schedule_handle = MagicMock()
        client.create_schedule = AsyncMock()

        with patch(
            "tfc.temporal.common.registry._import_temporal_activity_modules"
        ) as mock_import:
            await a_register_schedules(client, schedules=[], cleanup_orphans=False)

        mock_import.assert_called_once()


class TestBuildScheduleReadsRegistry:
    """_build_schedule_for_config must thread the activity's
    decorator-declared max_retries / retry_delay onto TaskRunnerInput."""

    def test_known_activity_propagates_retry_metadata(self):
        @temporal_activity(name="fixture_scheduled_activity", max_retries=2, retry_delay=15)
        def _scheduled_no_op():
            return None

        config = ScheduleConfig(
            schedule_id="fixture-schedule",
            activity_name="fixture_scheduled_activity",
            interval_seconds=300,
            queue="default",
        )

        schedule = _build_schedule_for_config(config)
        run_input = schedule.action.args[0]

        assert isinstance(run_input, TaskRunnerInput)
        assert run_input.max_retries == 2
        assert run_input.retry_delay == 15

    def test_unknown_activity_passes_none(self):
        config = ScheduleConfig(
            schedule_id="fixture-unknown",
            activity_name="never_registered_scheduled_activity",
            interval_seconds=300,
            queue="default",
        )

        schedule = _build_schedule_for_config(config)
        run_input = schedule.action.args[0]

        # No registry entry → both fields stay None; workflow uses DEFAULT_RETRY_POLICY.
        assert run_input.max_retries is None
        assert run_input.retry_delay is None


class _AsyncIterMock:
    """Minimal async iterator that yields a fixed list — mocks the value
    returned by ``client.list_schedules`` so ``a_list_schedules`` can iterate."""

    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._items:
            raise StopAsyncIteration
        return self._items.pop(0)
