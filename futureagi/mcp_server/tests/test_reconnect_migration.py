"""Pre-existing connection activity is journaled and restored by migration."""

import importlib
import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.models import Organization, OrgApiKey, User
from accounts.tests.api_secret_migration_probes import migrate_to_latest

BEFORE = ("mcp_server", "0004_remove_retired_tool_groups")
AFTER = ("mcp_server", "0005_personal_key_reconnect")


def migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    return executor.loader.project_state().apps


@pytest.mark.django_db(transaction=True)
def test_preexisting_active_and_inactive_connections_have_reversible_journals():
    org = Organization.objects.create(name="reconnect-migration")
    user = User.objects.create(
        email=f"{uuid.uuid4().hex}@example.invalid", organization=org
    )
    retired = OrgApiKey.no_workspace_objects.create(
        organization=org, user=user, type="mcp", retired_reason="733", enabled=False
    )
    personal = OrgApiKey.no_workspace_objects.create(
        organization=org, user=user, type="user"
    )
    try:
        migrate(BEFORE)
        executor = MigrationExecutor(connection)
        targets = [
            node
            for node in executor.loader.graph.leaf_nodes()
            if node[0] != "mcp_server"
        ] + [BEFORE]
        previous = executor.loader.project_state(targets).apps.get_model(
            "mcp_server", "MCPConnection"
        )
        rows = {}
        for active in (True, False):
            row = previous._base_manager.create(
                user_id=user.pk,
                organization_id=org.pk,
                api_key_id=retired.pk,
                is_active=active,
            )
            rows[row.pk] = active
        control = previous._base_manager.create(
            user_id=user.pk,
            organization_id=org.pk,
            api_key_id=personal.pk,
            is_active=True,
        )
        apps = migrate(AFTER)
        current = apps.get_model("mcp_server", "MCPConnection")
        assert "accounts_retirement_manifest" in connection.introspection.table_names()
        journal = apps.get_model("accounts", "RetirementManifest")
        original_journals = {}
        for pk, active in rows.items():
            row = current._base_manager.get(pk=pk)
            assert row.is_active is False and row.reconnect_required is True
            assert journal.objects.filter(
                service="mcp", action="reconnect", object_id=str(pk)
            ).exists()
            saved = journal.objects.get(
                service="mcp", action="reconnect", object_id=str(pk)
            )
            assert saved.prior_state == {
                "is_active": active,
                "reconnect_required": False,
            }
            assert saved.applied_state == {
                "is_active": False,
                "reconnect_required": True,
                "deleted": False,
                "updated_at": row.updated_at.isoformat(),
                "user_id": str(user.pk),
                "organization_id": str(org.pk),
                "api_key_id": str(retired.pk),
            }
            assert (
                saved.confirmed
                and saved.reverted_at is None
                and saved.superseded_by == ""
            )
            assert saved.applied_at is not None
            original_journals[pk] = saved.prior_state
        assert not journal.objects.filter(object_id=str(control.pk)).exists()
        control_row = current._base_manager.get(pk=control.pk)
        assert control_row.is_active and not control_row.reconnect_required
        module = importlib.import_module(
            "mcp_server.migrations.0005_personal_key_reconnect"
        )
        with connection.schema_editor() as editor:
            module.flag_reconnect(apps, editor)
        for pk, prior in original_journals.items():
            assert journal.objects.get(object_id=str(pk)).prior_state == prior
        migrate(BEFORE)
        for pk, active in rows.items():
            assert previous._base_manager.get(pk=pk).is_active is active
            assert journal.objects.get(object_id=str(pk)).reverted_at is not None
        migrate(AFTER)
        for pk, active in rows.items():
            saved = journal.objects.get(object_id=str(pk))
            assert saved.prior_state["is_active"] is active
            assert saved.reverted_at is None
    finally:
        migrate_to_latest()
