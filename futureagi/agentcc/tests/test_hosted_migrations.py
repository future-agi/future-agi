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


def test_hosted_phases_bind_actual_installed_graph():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    # Compare with disk, not a mock graph whose leaf copies the stale constant.
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


WEBHOOK_FENCED_MIGRATIONS = (
    ("prism", "0075_webhook_credential_columns"),
    ("prism", "0076_encrypt_webhook_credentials"),
    ("prism", "0077_remove_webhook_plaintext"),
)


def test_hosted_real_graph_applies_webhook_migrations_only_in_backfill():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    # The real planner only needs its loader; do not open a DB/recorder.
    executor = MigrationExecutor.__new__(MigrationExecutor)
    executor.loader = loader

    schema_plan = executor.migration_plan(phase_targets(loader, "schema"))
    validate_plan(loader, schema_plan, "schema")
    schema_keys = {
        (migration.app_label, migration.name) for migration, _ in schema_plan
    }
    assert SCHEMA_BOUNDARY == ("prism", "0046_org_config_db_defaults")
    assert set(WEBHOOK_FENCED_MIGRATIONS).isdisjoint(schema_keys)
    assert all(not backwards for _, backwards in schema_plan)
    loader.applied_migrations = dict.fromkeys(schema_keys)

    backfill_plan = executor.migration_plan(phase_targets(loader, "backfill"))
    validate_plan(loader, backfill_plan, "backfill")
    backfill_keys = [
        (migration.app_label, migration.name) for migration, _ in backfill_plan
    ]
    assert [key for key in backfill_keys if key in WEBHOOK_FENCED_MIGRATIONS] == list(
        WEBHOOK_FENCED_MIGRATIONS
    )
    assert RELEASE_BOUNDARY in backfill_keys
    assert all(not backwards for _, backwards in backfill_plan)
    with pytest.raises(CommandError, match="not fully applied"):
        validate_plan(
            loader, executor.migration_plan(phase_targets(loader, "check")), "check"
        )

    loader.applied_migrations.update(dict.fromkeys(backfill_keys))
    assert phase_targets(loader, "schema") == []
    for phase in PHASES:
        plan = executor.migration_plan(phase_targets(loader, phase))
        assert plan == []
        validate_plan(loader, plan, phase)


@pytest.mark.parametrize("phase", ("schema", "check"))
@pytest.mark.parametrize("migration_key", WEBHOOK_FENCED_MIGRATIONS)
def test_only_backfill_accepts_fenced_webhook_plan(phase, migration_key):
    loader = MigrationLoader(None, ignore_no_migrations=True)
    loader.applied_migrations = {
        SCHEMA_BOUNDARY: loader.disk_migrations[SCHEMA_BOUNDARY]
    }
    plan = [(loader.disk_migrations[migration_key], False)]
    with pytest.raises(CommandError):
        validate_plan(loader, plan, phase)
    validate_plan(loader, plan, "backfill")


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
