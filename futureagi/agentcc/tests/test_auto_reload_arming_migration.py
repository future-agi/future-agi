"""Run schema rewind/upgrade only in a dedicated synthetic PostgreSQL DB."""

import importlib
import importlib.util

import pytest
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor

from agentcc.tests import test_managed_credits_api as wallet

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
ledger = wallet.ledger
BEFORE = ("prism", "0079_auto_reload_payment_method")
AFTER = ("prism", "0080_auto_reload_arming")
MODULE = "agentcc.migrations.0080_auto_reload_arming"


def migration_module():
    assert importlib.util.find_spec(MODULE) is not None, "arming migration missing"
    return importlib.import_module(MODULE)


def test_arming_migration_is_only_an_empty_sidecar_schema():
    migration = migration_module().Migration
    assert migration.dependencies == [BEFORE]
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.CreateModel)
    assert operation.name == "AgentccAutoReloadArming"
    assert [name for name, _ in operation.fields] == [
        "organization",
        "epoch",
        "below_threshold",
        "intent_id",
    ]
    latch = dict(operation.fields)["intent_id"]
    assert latch.default == ""
    assert latch.max_length == 64


def test_arming_upgrade_does_not_backfill_accounts_receipts_or_money(
    ledger, organization
):
    migration_module()
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        ledger.ManagedCreditAccount.objects.create(organization=organization)
        accounts_before = list(
            ledger.ManagedCreditAccount.objects.order_by("pk").values()
        )
        before_apps = executor.loader.project_state([BEFORE]).apps
        receipts = before_apps.get_model("prism", "AgentccAutoReloadConsent")
        receipts_before = list(receipts.objects.order_by("pk").values())
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        after_apps = executor.loader.project_state([AFTER]).apps
        states = after_apps.get_model("prism", "AgentccAutoReloadArming")
        assert not states.objects.exists()
        assert (
            list(ledger.ManagedCreditAccount.objects.order_by("pk").values())
            == accounts_before
        )
        after_receipts = after_apps.get_model("prism", "AgentccAutoReloadConsent")
        assert list(after_receipts.objects.order_by("pk").values()) == receipts_before
    finally:
        MigrationExecutor(connection).migrate(restore)
