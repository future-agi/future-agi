"""The cleanup ledger is single-leaf, reversible, and preserves vault rows."""

import pytest
from django.apps import apps
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.state import ProjectState

PREV = ("prism", "0061_mcp_personal_principal_cleanup")
RECEIPTS = ("prism", "0062_mcp_cleanup_receipts")


def migrate(target):
    MigrationExecutor(connection).migrate([target])


@pytest.mark.django_db(transaction=True)
def test_cleanup_receipts_single_leaf_reverse_and_reapply(organization, user):
    loader = MigrationLoader(connection)
    # Later grant-revision, SIEM and adapter migrations share one leaf and
    # must be restored after exercising the cleanup-receipt migration itself.
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
    try:
        migrate(PREV)
        historical = MigrationLoader(connection).project_state(PREV).apps
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
            migrate(RECEIPTS)
            current = MigrationLoader(connection).project_state(RECEIPTS).apps
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
                migrate(PREV)
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
        migrate(leaf)
