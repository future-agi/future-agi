"""The deleted span-only operator commands must remain zero-I/O tombstones."""

from __future__ import annotations

import inspect

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from model_hub.apps import (
    OPERATOR_STARTUP_MUTATION_COMMANDS,
    STARTUP_SAFE_MANAGEMENT_COMMANDS,
)
from tracer.management.commands import (
    ch25_activate_attribute_catalog as activate_command,
)
from tracer.management.commands import (
    ch25_backfill_attribute_catalog as backfill_command,
)

REPLACEMENT = "ch25_property_catalog_dev_rollout"


@pytest.mark.parametrize("module", (backfill_command, activate_command))
def test_retired_attribute_catalog_command_fails_with_unified_replacement(
    module,
) -> None:
    with pytest.raises(CommandError, match=REPLACEMENT):
        module.Command().handle()


@pytest.mark.parametrize(
    ("command_name", "options"),
    (
        (
            "ch25_backfill_attribute_catalog",
            {"environment": "development", "dry_run": True},
        ),
        (
            "ch25_activate_attribute_catalog",
            {"environment": "development", "dry_run": True},
        ),
    ),
)
def test_stale_cli_options_reach_explicit_replacement_error(
    command_name: str,
    options: dict[str, object],
) -> None:
    with pytest.raises(CommandError, match=REPLACEMENT):
        call_command(command_name, **options)


@pytest.mark.parametrize("module", (backfill_command, activate_command))
def test_retired_attribute_catalog_command_imports_no_io_or_runtime(module) -> None:
    source = inspect.getsource(module)

    assert "clickhouse_connect" not in source
    assert "call_command" not in source
    assert "get_client" not in source
    assert "attribute_catalog_" not in source.replace(
        "ch25_activate_attribute_catalog", ""
    ).replace("ch25_backfill_attribute_catalog", "")


def test_operator_allowlist_contains_only_unified_catalog_rollout() -> None:
    assert REPLACEMENT in OPERATOR_STARTUP_MUTATION_COMMANDS
    assert "ch25_backfill_attribute_catalog" not in OPERATOR_STARTUP_MUTATION_COMMANDS
    assert "ch25_activate_attribute_catalog" not in OPERATOR_STARTUP_MUTATION_COMMANDS
    assert "ch25_backfill_attribute_catalog" in STARTUP_SAFE_MANAGEMENT_COMMANDS
    assert "ch25_activate_attribute_catalog" in STARTUP_SAFE_MANAGEMENT_COMMANDS
