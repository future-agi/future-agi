from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management.base import CommandError
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader

from agentcc.hosted_migrations import (
    PHASES,
    RELEASE_BOUNDARY,
    SCHEMA_BOUNDARY,
    phase_targets,
    validate_plan,
)
from tfc.migration_bridge import LEGACY_APPLIED_NAMES


def test_hosted_phases_bind_actual_installed_graph():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    # Bind the constant to the files Django actually loads, not to a mock or
    # another hand-maintained migration name. A new leaf must update the pin.
    assert loader.graph.leaf_nodes("prism") == [RELEASE_BOUNDARY]
    for phase in PHASES:
        expected = [SCHEMA_BOUNDARY] if phase == "schema" else loader.graph.leaf_nodes()
        assert phase_targets(loader, phase) == expected
    ancestry = set(loader.graph.forwards_plan(RELEASE_BOUNDARY))
    for name in (
        "0030_request_log_v2",
        "0046_org_config_db_defaults",
        "0047_encrypt_org_config_secrets",
        "0048_r7_batch_jobs",
        "0048_r7_email_alert_state",
        "0049_merge_r7",
        "0050_encrypt_org_config_secret_aliases",
        "0051_encrypt_alert_channel_urls",
        "0029_request_log_unique_request_id",
        "0052_auto_reload_consent",
        "0057_mcp_credential_origin",
    ):
        assert ("prism", name) in ancestry


WEBHOOK_MIGRATIONS = (
    ("prism", "0075_webhook_credential_columns"),
    ("prism", "0076_encrypt_webhook_credentials"),
    ("prism", "0077_remove_webhook_plaintext"),
)


def _disk_executor():
    # migration_plan only needs a loader. Avoid constructing a DB recorder,
    # while exercising Django's real forward/backward planner on disk files.
    executor = object.__new__(MigrationExecutor)
    executor.loader = MigrationLoader(None, ignore_no_migrations=True)
    return executor


@pytest.mark.parametrize("phase", PHASES)
def test_real_graph_allows_webhook_migrations_only_in_backfill(phase):
    executor = _disk_executor()
    loader = executor.loader
    assert SCHEMA_BOUNDARY == ("prism", "0046_org_config_db_defaults")
    if phase != "schema":
        loader.applied_migrations = dict.fromkeys(
            loader.graph.forwards_plan(SCHEMA_BOUNDARY)
        )
    plan = executor.migration_plan(phase_targets(loader, phase))
    assert all(not backwards for _, backwards in plan)
    nodes = [(migration.app_label, migration.name) for migration, _ in plan]
    if phase == "schema":
        assert set(WEBHOOK_MIGRATIONS).isdisjoint(nodes)
        validate_plan(loader, plan, phase)
    else:
        # All three must be planned in order; a pending check plan is refused
        # before it could execute any of them. Only backfill accepts the plan.
        assert [node for node in nodes if node in WEBHOOK_MIGRATIONS] == list(
            WEBHOOK_MIGRATIONS
        )
        if phase == "check":
            with pytest.raises(CommandError, match="not fully applied"):
                validate_plan(loader, plan, phase)
        else:
            validate_plan(loader, plan, phase)
        webhook_plan = [
            (loader.disk_migrations[node], False) for node in WEBHOOK_MIGRATIONS
        ]
        with pytest.raises(CommandError, match="encryption boundary"):
            validate_plan(loader, webhook_plan, "schema")


@pytest.mark.parametrize("phase", PHASES)
def test_real_graph_phase_reruns_after_backfill_are_forward_only_noops(phase):
    executor = _disk_executor()
    loader = executor.loader
    loader.applied_migrations = loader.graph.nodes.copy()
    targets = phase_targets(loader, phase)
    if phase == "schema":
        assert targets == []
    plan = executor.migration_plan(targets)
    assert plan == []
    validate_plan(loader, plan, phase)


def test_hosted_phase_never_crosses_encryption_before_runtime_overlap():
    loader = MagicMock()
    loader.applied_migrations = {SCHEMA_BOUNDARY}
    loader.disk_migrations = {SCHEMA_BOUNDARY, RELEASE_BOUNDARY}
    loader.graph.forwards_plan.return_value = [SCHEMA_BOUNDARY]
    migration = SimpleNamespace(app_label="prism", name=RELEASE_BOUNDARY[1])
    with pytest.raises(CommandError, match="encryption boundary"):
        validate_plan(loader, [(migration, False)], "schema")
    validate_plan(loader, [(migration, False)], "backfill")
    with pytest.raises(CommandError, match="never reverse"):
        validate_plan(loader, [(migration, True)], "backfill")
    with pytest.raises(CommandError, match="not fully applied"):
        validate_plan(loader, [(migration, False)], "check")
    loader.applied_migrations = set()
    with pytest.raises(CommandError, match="Schema phase"):
        validate_plan(loader, [(migration, False)], "backfill")


def test_hosted_phase_refuses_unknown_historical_migration_names():
    loader = MagicMock()
    loader.applied_migrations = {("prism", "historical_connector_name")}
    loader.disk_migrations = {SCHEMA_BOUNDARY, RELEASE_BOUNDARY}
    with pytest.raises(CommandError, match="applied-state review"):
        validate_plan(loader, [], "schema")


def test_bridge_release_boundary_is_single_disk_leaf():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    leaves = loader.graph.leaf_nodes("prism")
    assert len(leaves) == 1
    assert RELEASE_BOUNDARY == leaves[0]


@pytest.mark.parametrize("phase", PHASES)
def test_bridge_legacy_applied_names_are_known(phase):
    loader = MigrationLoader(None, ignore_no_migrations=True)
    # A bridged database retains every c2.5 name, across all app labels,
    # alongside the c2.6 names recorded by the forward migration executor.
    legacy = set(LEGACY_APPLIED_NAMES.values())
    assert legacy - set(loader.disk_migrations)
    loader.applied_migrations = dict.fromkeys(set(loader.disk_migrations) | legacy)
    validate_plan(loader, [], phase)


@pytest.mark.parametrize("phase", PHASES)
def test_bridge_phases_still_refuse_unknown_prism_history(phase):
    executor = _disk_executor()
    loader = executor.loader
    legacy = set(LEGACY_APPLIED_NAMES.values())
    unknown = ("prism", "historical_connector_name")
    assert unknown not in loader.disk_migrations
    assert unknown not in legacy
    loader.applied_migrations = dict.fromkeys(
        set(loader.disk_migrations) | legacy | {unknown}
    )
    # Exercise the real phase entry path on an otherwise fully applied graph.
    plan = executor.migration_plan(phase_targets(loader, phase))
    assert plan == []
    with pytest.raises(CommandError, match="Unknown applied prism migration"):
        validate_plan(loader, plan, phase)
