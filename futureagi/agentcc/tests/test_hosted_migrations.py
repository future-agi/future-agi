from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.core.management.base import CommandError
from django.db.migrations.loader import MigrationLoader

from agentcc.hosted_migrations import (
    RELEASE_BOUNDARY,
    SCHEMA_BOUNDARY,
    phase_targets,
    validate_plan,
)


def test_hosted_phases_bind_actual_installed_graph():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert phase_targets(loader, "schema") == [SCHEMA_BOUNDARY]
    assert RELEASE_BOUNDARY in phase_targets(loader, "backfill")
    assert len([node for node in loader.graph.leaf_nodes() if node[0] == "prism"]) == 1
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
