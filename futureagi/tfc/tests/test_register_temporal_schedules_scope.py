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
        "cleanup_orphans": False,
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


@pytest.mark.parametrize("cleanup_orphans", [False, True])
@pytest.mark.asyncio
async def test_full_registration_only_cleans_orphans_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_orphans: bool,
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

    await command_module.Command()._handle_async(
        _options(cleanup_orphans=cleanup_orphans)
    )

    register.assert_awaited_once_with(
        client, [schedule], cleanup_orphans=cleanup_orphans
    )


@pytest.mark.parametrize(
    ("arguments", "expected"), [([], False), (["--cleanup-orphans"], True)]
)
def test_cleanup_orphans_parser_default_and_opt_in(arguments, expected):
    parser = command_module.Command().create_parser(
        "manage.py", "register_temporal_schedules"
    )

    assert parser.parse_args(arguments).cleanup_orphans is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        ["--model-hub-only"],
        ["--list"],
        ["--delete-all"],
        ["--pause", "schedule-id"],
        ["--unpause", "schedule-id"],
        ["--trigger", "schedule-id"],
        ["--describe", "schedule-id"],
        ["--pause", ""],
        ["--unpause", ""],
        ["--trigger", ""],
        ["--describe", ""],
    ],
)
async def test_cleanup_conflicts_fail_before_temporal_client(monkeypatch, arguments):
    command = command_module.Command()
    parser = command.create_parser("manage.py", "register_temporal_schedules")
    options = vars(parser.parse_args(["--cleanup-orphans", *arguments]))
    get_client = AsyncMock()
    register = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", get_client)
    monkeypatch.setattr(command_module, "a_register_schedules", register)

    with pytest.raises(CommandError, match="--cleanup-orphans"):
        await command._handle_async(options)

    get_client.assert_not_awaited()
    register.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["--pause", "--unpause", "--trigger", "--describe"])
@pytest.mark.parametrize("scope", [[], ["--model-hub-only"]])
async def test_empty_action_id_cannot_fall_through_to_registration(
    monkeypatch, action, scope
):
    command = command_module.Command()
    parser = command.create_parser("manage.py", "register_temporal_schedules")
    get_client = AsyncMock()
    register = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", get_client)
    monkeypatch.setattr(command_module, "a_register_schedules", register)

    with pytest.raises(CommandError):
        await command._handle_async(vars(parser.parse_args([*scope, action, ""])))

    get_client.assert_not_awaited()
    register.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("arguments", "method", "schedule_id"),
    [
        (["--list"], "_list_schedules", None),
        (["--delete-all"], "_delete_all_schedules", None),
        (["--pause", "schedule-id"], "_pause_schedule", "schedule-id"),
        (["--unpause", "schedule-id"], "_unpause_schedule", "schedule-id"),
        (["--trigger", "schedule-id"], "_trigger_schedule", "schedule-id"),
        (["--describe", "schedule-id"], "_describe_schedule", "schedule-id"),
    ],
)
async def test_explicit_actions_keep_their_existing_dispatch(
    monkeypatch, arguments, method, schedule_id
):
    command = command_module.Command()
    parser = command.create_parser("manage.py", "register_temporal_schedules")
    client = object()
    action = AsyncMock()
    register = AsyncMock()
    monkeypatch.setattr(command_module, "get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(command_module, "a_register_schedules", register)
    monkeypatch.setattr(command, method, action)

    await command._handle_async(vars(parser.parse_args(arguments)))

    if schedule_id is None:
        action.assert_awaited_once_with(client)
    else:
        action.assert_awaited_once_with(client, schedule_id)
    register.assert_not_awaited()


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
