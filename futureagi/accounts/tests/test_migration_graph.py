"""The migration graph after merging lines: one leaf per app, and tests that walk
accounts backwards put every leaf back.

On 2026-10-19 omega/integration gained two children of accounts 0025:
0026_host_event_outbox (READINESS lane 1.2a) and 0026_orgapikey_secret_hash plus
0027_seal_api_secrets (L03). Two leaves make ``migrate`` refuse ("Conflicting
migrations detected"), so test databases cannot be created; 0028_merge_20261019_1400
joins them. Integration round 7 brought dev's 0028_merge_authtoken_scope_and_simulation_agent_key
beside the lifecycle migration 0029_omega_purge_lifecycle; 0030_merge_omega_purge_and_simulation_agent_key
joins them. 0031_retire_org_keys follows that merge and is now the single leaf.
The API-secret migration tests migrate to 0025 and back; going back to 0027 alone
would leave the outbox unapplied for every later test on that database.
"""

import pytest
from django.db import connection
from django.db.migrations.loader import MigrationLoader

from accounts.tests.api_secret_migration_probes import (
    BEFORE,
    applied,
    migrate_to,
    migrate_to_latest,
)

OUTBOX = ("accounts", "0026_host_event_outbox")


def test_no_app_has_two_migration_leaves():
    loader = MigrationLoader(None, ignore_no_migrations=True)
    assert loader.detect_conflicts() == {}
    retirement = ("accounts", "0031_retire_org_keys")
    merge = ("accounts", "0030_merge_omega_purge_and_simulation_agent_key")
    journal = ("accounts", "0034_retirement_manifest")
    assert loader.graph.leaf_nodes("accounts") == [journal]
    assert loader.disk_migrations[retirement].dependencies == [merge]
    assert merge in loader.graph.forwards_plan(retirement)
    assert retirement in loader.graph.forwards_plan(journal)
    reconnect = ("mcp_server", "0005_personal_key_reconnect")
    assert journal in loader.disk_migrations[reconnect].dependencies


@pytest.mark.django_db(transaction=True)
def test_restoring_after_a_walk_to_0025_reapplies_every_accounts_leaf():
    migrate_to(BEFORE)
    try:
        assert not applied(OUTBOX)
    finally:
        migrate_to_latest()
    loader = MigrationLoader(connection)
    # Every app's leaf: other apps depend on accounts after 0025 (round 7), and
    # the walk back unapplies them as well.
    for leaf in loader.graph.leaf_nodes():
        for node in loader.graph.forwards_plan(leaf):
            assert node in loader.applied_migrations, node
    with connection.cursor() as cursor:
        tables = connection.introspection.table_names(cursor)
    assert "accounts_host_event_outbox" in tables
