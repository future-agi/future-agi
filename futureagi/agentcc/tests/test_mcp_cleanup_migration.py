"""The cleanup ledger is single-leaf, reversible, and preserves vault rows."""

from unittest.mock import patch

import pytest
from django.apps import apps
from django.db import connection, connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState

PREV = ("prism", "0061_mcp_personal_principal_cleanup")
RECEIPTS = ("prism", "0062_mcp_cleanup_receipts")


class _Alone:
    """``target`` unapplied and re-applied by itself, through the migration
    executor (the migration boundary included), on its own project state.
    Nothing else is migrated: 0075-0077 refuse their reverse by design, so a
    database at today's leaf cannot go back to ``target`` as a whole graph.
    The recorded history is left at the leaf, and ``restore`` re-applies
    ``target`` if a check failed while it was unapplied."""

    def __init__(self, target):
        self.target = target
        self.applied = True

    def _run(self, method):
        executor = MigrationExecutor(connections["default"])
        migration = executor.loader.get_migration(*self.target)
        state = executor.loader.project_state(self.target, at_end=False)
        with (
            patch.object(executor, "record_migration"),
            patch.object(executor.recorder, "record_unapplied"),
        ):
            getattr(executor, method)(state, migration)

    def unapply(self):
        self._run("unapply_migration")
        self.applied = False

    def apply(self):
        self._run("apply_migration")
        self.applied = True

    def restore(self):
        if not self.applied:
            self.apply()


@pytest.mark.django_db(transaction=True)
def test_cleanup_receipts_single_leaf_reverse_and_reapply(organization, user):
    loader = MigrationLoader(connections["default"])
    # Later grant-revision, SIEM and adapter migrations share one leaf; the
    # cleanup-receipt migration is exercised alone, under that leaf.
    [leaf] = loader.graph.leaf_nodes("prism")
    assert RECEIPTS in loader.graph.forwards_plan(leaf)
    assert not loader.detect_conflicts()
    assert (
        not MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
        )
        .changes(graph=loader.graph)
        .get("prism")
    )
    receipts = _Alone(RECEIPTS)
    try:
        receipts.unapply()
        historical = MigrationLoader(connections["default"]).project_state(PREV).apps
        Cred = historical.get_model("prism", "AgentccMCPCredential")
        row = Cred.objects.create(
            organization_id=organization.pk,
            user_id=user.pk,
            scope="user",
            server_id="removed-server",
            kind="oauth",
            status="revoked",
            encrypted_secret="historical-test-access",
            encrypted_refresh="historical-test-refresh",
            oauth_revoke_pending=True,
            gateway_purge_pending=True,
        )
        for turn in range(2):
            receipts.apply()
            current = (
                MigrationLoader(connections["default"]).project_state(RECEIPTS).apps
            )
            upgraded = current.get_model("prism", "AgentccMCPCredential").objects.get(
                pk=row.pk
            )
            assert upgraded.oauth_revoke_outcome == "pending"
            Receipt = current.get_model("prism", "AgentccMCPCleanupReceipt")
            assert set(
                Receipt.objects.filter(credential_id=row.pk).values_list(
                    "trigger",
                    "step",
                    "outcome",
                    "reason",
                )
            ) == {
                ("migration", "vendor_revocation", "pending", "recorded"),
                ("migration", "gateway_purge", "pending", "recorded"),
            }
            assert upgraded.encrypted_refresh == "historical-test-refresh"
            if turn == 0:
                receipts.unapply()
                assert Cred.objects.filter(pk=row.pk).exists()
                assert (
                    "agentcc_mcp_cleanup_receipt"
                    not in connection.introspection.table_names()
                )
                with connection.cursor() as cursor:
                    columns = {
                        c.name
                        for c in connection.introspection.get_table_description(
                            cursor,
                            "agentcc_mcp_credential",
                        )
                    }
                assert "oauth_revoke_outcome" not in columns
    finally:
        receipts.restore()
    assert "agentcc_mcp_cleanup_receipt" in connection.introspection.table_names()
