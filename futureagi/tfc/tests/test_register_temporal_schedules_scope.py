from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from django.core.management.base import CommandError

from tfc.management.commands import register_temporal_schedules as command_module
from tfc.temporal.property_catalog_queue import PROPERTY_CATALOG_TASK_QUEUE
from tfc.temporal.schedules.config import ScheduleConfig


def _options(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "list": False,
        "delete_all": False,
        "model_hub_only": False,
        "property_catalog_only": False,
        "pause": None,
        "unpause": None,
        "trigger": None,
        "describe": None,
    }
    values.update(overrides)
    return values


@pytest.mark.asyncio
async def test_property_catalog_only_registers_exact_reviewed_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schedule = ScheduleConfig(
        schedule_id="unified-property-catalog-dev-workspace",
        activity_name="reconcile_unified_property_catalog_dev",
        interval_seconds=120,
        queue=PROPERTY_CATALOG_TASK_QUEUE,
    )
    client = object()
    get_client = AsyncMock(return_value=client)
    register = AsyncMock()
    monkeypatch.setattr(command_module, "PROPERTY_CATALOG_SCHEDULES", [schedule])
    monkeypatch.setattr(command_module, "get_client", get_client)
    monkeypatch.setattr(command_module, "a_register_schedules", register)

    await command_module.Command()._handle_async(_options(property_catalog_only=True))

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
        _options(model_hub_only=True, property_catalog_only=True),
        _options(property_catalog_only=True, list=True),
    ],
)
async def test_property_catalog_scope_conflicts_fail_before_temporal_client(
    monkeypatch: pytest.MonkeyPatch,
    options: dict[str, object],
) -> None:
    get_client = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", get_client)

    with pytest.raises(CommandError):
        await command_module.Command()._handle_async(options)

    get_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_property_catalog_only_refuses_zero_or_multiple_schedules_before_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_client = AsyncMock()
    monkeypatch.setattr(command_module, "PROPERTY_CATALOG_SCHEDULES", [])
    monkeypatch.setattr(command_module, "get_client", get_client)

    with pytest.raises(CommandError, match="exactly one"):
        await command_module.Command()._handle_async(
            _options(property_catalog_only=True)
        )

    get_client.assert_not_awaited()
