from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from django.core.management.base import CommandError

from tfc.management.commands import register_temporal_schedules as command_module
from tfc.temporal.schedules.config import ScheduleConfig


def _options(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "list": False,
        "delete_all": False,
        "model_hub_only": False,
        "pause": None,
        "unpause": None,
        "trigger": None,
        "describe": None,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_model_hub_only_registers_without_cleaning_other_schedules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = ScheduleConfig(
        schedule_id="model-hub-schedule",
        activity_name="model_hub_activity",
        interval_seconds=120,
        queue="default",
    )
    client = object()
    get_client = AsyncMock(return_value=client)
    register = AsyncMock()
    monkeypatch.setattr(command_module, "MODEL_HUB_SCHEDULES", [schedule])
    monkeypatch.setattr(command_module, "get_client", get_client)
    monkeypatch.setattr(command_module, "a_register_schedules", register)

    await command_module.Command()._handle_async(_options(model_hub_only=True))

    get_client.assert_awaited_once_with()
    register.assert_awaited_once_with(client, [schedule], cleanup_orphans=False)


@pytest.mark.asyncio
async def test_full_registration_still_cleans_orphaned_schedules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = ScheduleConfig(
        schedule_id="regular-schedule",
        activity_name="regular_activity",
        interval_seconds=120,
        queue="regular-queue",
    )
    client = object()
    get_client = AsyncMock(return_value=client)
    register = AsyncMock()
    monkeypatch.setattr(command_module, "ALL_SCHEDULES", [schedule])
    monkeypatch.setattr(command_module, "get_client", get_client)
    monkeypatch.setattr(command_module, "a_register_schedules", register)

    await command_module.Command()._handle_async(_options())

    register.assert_awaited_once_with(client, [schedule], cleanup_orphans=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        _options(model_hub_only=True, list=True),
        _options(model_hub_only=True, trigger="schedule-id"),
    ],
)
async def test_model_hub_scope_conflicts_fail_before_temporal_client(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
) -> None:
    get_client = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", get_client)

    with pytest.raises(CommandError):
        await command_module.Command()._handle_async(options)

    get_client.assert_not_awaited()
