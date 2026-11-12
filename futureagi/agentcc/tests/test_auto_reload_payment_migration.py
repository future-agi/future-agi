"""Run in a dedicated approved-venue database; never against a serving database."""

import importlib
import importlib.util

import pytest
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
MODULE = "agentcc.migrations.0079_auto_reload_payment_method"
BEFORE = ("prism", "0078_historical_key_revocations")
AFTER = ("prism", "0079_auto_reload_payment_method")


def migration_module():
    assert importlib.util.find_spec(MODULE) is not None, (
        "payment binding migration missing"
    )
    return importlib.import_module(MODULE)


def test_migration_is_schema_only_and_has_no_consent_backfill():
    migration = migration_module().Migration
    assert migration.dependencies == [BEFORE]
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.AddField)
    assert operation.model_name == "agentccautoreloadconsent"
    assert operation.name == "payment_method_id"
    assert operation.field.default == ""
    assert operation.field.max_length == 255


def test_upgrade_leaves_existing_receipt_unbound_and_does_not_mint_consent(
    organization,
):
    migration_module()
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        old_apps = executor.loader.project_state([BEFORE]).apps
        old_receipts = old_apps.get_model("prism", "AgentccAutoReloadConsent")
        old_receipts.objects.create(
            organization_id=organization.pk,
            actor_id=None,
            actor_identifier="00000000-0000-4000-8000-000000000001",
            consent_version="auto-reload-v1",
            accepted_at=timezone.now(),
            threshold_micros=10_000_000,
            amount_micros=25_000_000,
        )
        before = list(old_receipts.objects.order_by("id").values())
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        new_apps = executor.loader.project_state([AFTER]).apps
        new_receipts = new_apps.get_model("prism", "AgentccAutoReloadConsent")
        after = list(new_receipts.objects.order_by("id").values())
        assert len(after) == len(before)
        assert all(row.pop("payment_method_id") == "" for row in after)
        assert after == before
    finally:
        MigrationExecutor(connection).migrate(restore)
