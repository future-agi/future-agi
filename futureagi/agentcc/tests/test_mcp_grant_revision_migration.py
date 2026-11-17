"""Shared credentials get grant revisions in creation order, reversibly."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection, connections
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.utils import timezone

PREV = ("prism", "0062_mcp_cleanup_receipts")
REVISIONS = ("prism", "0063_mcp_credential_grant_revision")


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
def test_existing_shared_credentials_are_numbered_per_slot_in_creation_order(
    organization, user
):
    revisions = _Alone(REVISIONS)
    try:
        revisions.unapply()
        historical = MigrationLoader(connections["default"]).project_state(PREV).apps
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
            revisions.apply()
            current = (
                MigrationLoader(connections["default"]).project_state(REVISIONS).apps
            )
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
                revisions.unapply()
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
        revisions.restore()
    with connection.cursor() as cursor:
        columns = {
            c.name
            for c in connection.introspection.get_table_description(
                cursor, "agentcc_mcp_credential"
            )
        }
    assert "grant_revision" in columns
