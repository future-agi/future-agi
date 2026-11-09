"""Shared credentials get grant revisions in creation order, reversibly."""

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

PREV = ("prism", "0062_mcp_cleanup_receipts")
REVISIONS = ("prism", "0063_mcp_credential_grant_revision")


def migrate(target):
    MigrationExecutor(connection).migrate([target])


@pytest.mark.django_db(transaction=True)
def test_existing_shared_credentials_are_numbered_per_slot_in_creation_order(
    organization, user
):
    [leaf] = MigrationLoader(connection).graph.leaf_nodes("prism")
    try:
        migrate(PREV)
        historical = MigrationLoader(connection).project_state(PREV).apps
        Cred = historical.get_model("prism", "AgentccMCPCredential")
        start = timezone.now() - timedelta(days=1)

        def row(server_id, minutes, status, scope="shared"):
            created = Cred.objects.create(
                organization_id=organization.pk,
                user_id=user.pk if scope == "user" else None,
                scope=scope,
                server_id=server_id,
                kind="bearer",
                status=status,
                encrypted_secret="historical-test-secret",
            )
            Cred.objects.filter(pk=created.pk).update(
                created_at=start + timedelta(minutes=minutes)
            )
            return created.pk

        # Created out of id order: the revision follows creation time.
        github_old = row("github", 2, "revoked")
        github_new = row("github", 5, "active")
        github_first = row("github", 1, "revoked")
        docs = row("docs", 3, "active")
        personal = row("github", 4, "active", scope="user")
        for turn in range(2):
            migrate(REVISIONS)
            current = MigrationLoader(connection).project_state(REVISIONS).apps
            Current = current.get_model("prism", "AgentccMCPCredential")
            revision = dict(Current.objects.values_list("pk", "grant_revision"))
            # A slot's active credential is its newest, so it outranks every
            # older one; another server and a personal row are unaffected.
            assert (
                revision[github_first],
                revision[github_old],
                revision[github_new],
            ) == (1, 2, 3)
            assert revision[docs] == 1
            assert revision[personal] == 0
            if turn == 0:
                migrate(PREV)
                with connection.cursor() as cursor:
                    columns = {
                        c.name
                        for c in connection.introspection.get_table_description(
                            cursor, "agentcc_mcp_credential"
                        )
                    }
                assert "grant_revision" not in columns
                assert Cred.objects.filter(pk=github_new).exists()
    finally:
        migrate(leaf)
