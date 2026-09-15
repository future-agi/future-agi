"""
Tests for the schedule manager's contract with the activity registry.

The fix: ``a_register_schedules`` must call ``_import_temporal_activity_modules``
before iterating ScheduleConfig entries, otherwise ``_ACTIVITY_REGISTRY`` is
empty when ``_build_schedule_for_config`` runs and every schedule falls back
to ``DEFAULT_RETRY_POLICY`` regardless of decorator-declared max_retries.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from temporalio.client import ScheduleSpec

from tfc.temporal.drop_in.decorator import _ACTIVITY_REGISTRY, temporal_activity
from tfc.temporal.drop_in.workflow import TaskRunnerInput
from tfc.temporal.schedules.config import ScheduleConfig
from tfc.temporal.schedules.manager import (
    _build_schedule_for_config,
    a_register_schedules,
    a_update_schedule,
    register_schedules,
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
        @temporal_activity(
            name="fixture_scheduled_activity", max_retries=2, retry_delay=15
        )
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

    def test_jitter_is_passed_to_temporal_schedule_spec(self):
        config = ScheduleConfig(
            schedule_id="fixture-jitter",
            activity_name="never_registered_scheduled_activity",
            interval_seconds=300,
            jitter_seconds=45,
            queue="default",
        )

        schedule = _build_schedule_for_config(config)

        assert schedule.spec.jitter == timedelta(seconds=45)

    def test_activity_arguments_are_pinned_in_schedule_input(self):
        config = ScheduleConfig(
            schedule_id="fixture-arguments",
            activity_name="never_registered_scheduled_activity",
            interval_seconds=300,
            activity_args=("workspace",),
            activity_kwargs={"mode": "incremental"},
        )

        schedule = _build_schedule_for_config(config)
        run_input = schedule.action.args[0]

        assert run_input.args == ["workspace"]
        assert run_input.kwargs == {"mode": "incremental"}


class TestUpdateSchedule:
    @pytest.mark.asyncio
    async def test_update_preserves_timezone_without_overwriting_new_jitter(self):
        client = MagicMock()
        handle = MagicMock()
        client.get_schedule_handle.return_value = handle

        async def update(updater):
            existing_spec = ScheduleSpec(time_zone_name="America/New_York")
            existing_schedule = MagicMock()
            existing_schedule.spec = existing_spec
            description = MagicMock()
            description.schedule = existing_schedule
            return await updater(MagicMock(description=description))

        handle.update = AsyncMock(side_effect=update)
        schedule = _build_schedule_for_config(
            ScheduleConfig(
                schedule_id="fixture-jitter",
                activity_name="never_registered_scheduled_activity",
                interval_seconds=300,
                jitter_seconds=45,
                queue="default",
            )
        )

        await a_update_schedule(client, "fixture-jitter", schedule)

        assert schedule.spec.time_zone_name == "America/New_York"
        assert schedule.spec.jitter == timedelta(seconds=45)


@pytest.fixture
def schedule_inventory(monkeypatch):
    """Fake only Temporal's boundary; registration/cleanup/build/update stay real."""
    retained = {"unified-property-catalog-dev", "operator-kept-schedule"}
    configs = [
        ScheduleConfig(
            schedule_id=schedule_id,
            activity_name="fixture_scheduled_activity",
            interval_seconds=300,
            queue="default",
        )
        for schedule_id in ("current-schedule", "new-schedule")
    ]
    stored = {
        schedule_id: _build_schedule_for_config(
            ScheduleConfig(
                schedule_id=schedule_id,
                activity_name="previous_activity",
                interval_seconds=60,
                queue="default",
            )
        )
        for schedule_id in sorted(retained | {"current-schedule"})
    }
    original = dict(stored)
    handles = {}

    def get_handle(schedule_id):
        if schedule_id not in handles:

            async def describe():
                return SimpleNamespace(schedule=stored[schedule_id])

            async def update(updater):
                result = await updater(SimpleNamespace(description=await describe()))
                stored[schedule_id] = result.schedule

            async def delete():
                del stored[schedule_id]

            handles[schedule_id] = MagicMock(
                describe=AsyncMock(side_effect=describe),
                update=AsyncMock(side_effect=update),
                delete=AsyncMock(side_effect=delete),
            )
        return handles[schedule_id]

    async def create(schedule_id, schedule, *, trigger_immediately):
        assert schedule_id not in stored
        assert trigger_immediately is False
        stored[schedule_id] = schedule

    client = MagicMock()
    client.get_schedule_handle.side_effect = get_handle
    client.list_schedules = AsyncMock(
        side_effect=lambda: _AsyncIterMock(
            [SimpleNamespace(id=schedule_id) for schedule_id in stored]
        )
    )
    client.create_schedule = AsyncMock(side_effect=create)
    monkeypatch.setattr(
        "tfc.temporal.common.registry._import_temporal_activity_modules", MagicMock()
    )
    return client, configs, retained, stored, original, handles


@pytest.mark.parametrize("entrypoint", ["async", "sync"])
@pytest.mark.parametrize("cleanup_orphans", [None, False, True])
def test_registration_preserves_unknown_schedules_unless_cleanup_is_explicit(
    schedule_inventory, entrypoint, cleanup_orphans
):
    client, configs, retained, stored, original, handles = schedule_inventory
    # Omission independently exercises each public function's default.
    options = {} if cleanup_orphans is None else {"cleanup_orphans": cleanup_orphans}
    if entrypoint == "async":
        asyncio.run(a_register_schedules(client, configs, **options))
    else:
        register_schedules(client, configs, **options)

    expected_ids = {"current-schedule", "new-schedule"}
    if cleanup_orphans:
        client.list_schedules.assert_awaited_once_with()
        for schedule_id in retained:
            handles[schedule_id].delete.assert_awaited_once_with()
    else:
        expected_ids |= retained
        client.list_schedules.assert_not_called()
        client.list_schedules.assert_not_awaited()
        for schedule_id in retained:
            assert stored[schedule_id] is original[schedule_id]
            assert schedule_id not in handles
    assert set(stored) == expected_ids
    handles["current-schedule"].update.assert_awaited_once()
    handles["current-schedule"].delete.assert_not_awaited()
    handles["new-schedule"].delete.assert_not_awaited()
    client.create_schedule.assert_awaited_once_with(
        "new-schedule", stored["new-schedule"], trigger_immediately=False
    )
    for schedule_id in ("current-schedule", "new-schedule"):
        assert stored[schedule_id].spec.intervals[0].every == timedelta(seconds=300)
        assert (
            stored[schedule_id].action.args[0].activity_name
            == "fixture_scheduled_activity"
        )


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
