"""Exercise the retirement DDL and its reverse, not just RunPython functions."""

import uuid

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from accounts.api_secrets import hash_secret, seal
from accounts.tests.api_secret_migration_probes import migrate_to_latest, two_tenants

BEFORE = ("accounts", "0030_merge_omega_purge_and_simulation_agent_key")
AFTER = ("accounts", "0031_retire_org_keys")


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    return executor.loader.project_state([target]).apps.get_model("accounts", "OrgApiKey")


def _columns():
    with connection.cursor() as cursor:
        return {
            column.name
            for column in connection.introspection.get_table_description(
                cursor, "accounts_orgapikey"
            )
        }


@pytest.mark.django_db(transaction=True)
def test_retirement_upgrade_and_rollback_keep_every_tenants_state_and_hashes():
    tenants = two_tenants()
    retirement_columns = {"retired_at", "retired_reason", "prior_enabled"}
    try:
        PreviousKey = _migrate(BEFORE)
        assert not retirement_columns.intersection(_columns())
        original = {}
        for tenant_index, (org, owner) in enumerate(tenants):
            for kind in ("system", "mcp", "ingest", "user"):
                states = ((True, False), (False, False), (False, True))
                if kind == "system":
                    # The historical partial unique index permits one live
                    # system row per org. Across the two orgs exercise both
                    # enabled states, plus a deleted row in each.
                    states = ((tenant_index == 0, False), (False, True))
                for enabled, deleted in states:
                    secret = uuid.uuid4().hex
                    fields = seal(secret, recoverable=kind in ("system", "ingest"))
                    row = PreviousKey._base_manager.create(
                        organization_id=org.pk,
                        user_id=owner.pk if kind in ("user", "mcp") else None,
                        type=kind,
                        api_key=uuid.uuid4().hex,
                        enabled=enabled,
                        deleted=deleted,
                        **fields,
                    )
                    original[row.pk] = {
                        "organization_id": org.pk,
                        "user_id": row.user_id,
                        "type": kind,
                        "enabled": enabled,
                        "deleted": deleted,
                        **fields,
                    }
                    assert fields["secret_key_hash"] == hash_secret(secret)
        assert len(original) == 22

        RetiredKey = _migrate(AFTER)
        assert retirement_columns <= _columns()
        assert RetiredKey._base_manager.count() == len(original)
        for row in RetiredKey._base_manager.all():
            old = original[row.pk]
            for name, value in old.items():
                if name != "enabled":
                    assert getattr(row, name) == value, name
            if row.type == "user":
                assert row.enabled == old["enabled"]
                assert (row.retired_at, row.retired_reason, row.prior_enabled) == (
                    None, None, None
                )
            else:
                assert row.enabled is False
                assert row.prior_enabled == old["enabled"]
                assert row.retired_reason == "733"
                assert row.retired_at is not None

        RestoredKey = _migrate(BEFORE)
        assert not retirement_columns.intersection(_columns())
        assert RestoredKey._base_manager.count() == len(original)
        for row in RestoredKey._base_manager.all():
            for name, value in original[row.pk].items():
                assert getattr(row, name) == value, name
    finally:
        migrate_to_latest()
