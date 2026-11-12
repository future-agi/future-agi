"""Forward 0081 upgrade in a separate disposable PostgreSQL migration DB."""

import importlib

import pytest
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor

from agentcc.tests import test_managed_credits_api as wallet

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
ledger = wallet.ledger
BEFORE = ("prism", "0080_auto_reload_arming")
AFTER = ("prism", "0081_auto_reload_intent_exclusion")
MODULE = "agentcc.migrations.0081_auto_reload_intent_exclusion"


def test_intent_constraint_is_in_new_forward_migration_and_one_leaf():
    migration = importlib.import_module(MODULE).Migration
    assert migration.dependencies == [BEFORE]
    constraints = {
        operation.constraint.name: operation.constraint
        for operation in migration.operations
        if isinstance(operation, migrations.AddConstraint)
    }
    exclusion = constraints["agentcc_reload_one_nonterminal"]
    assert exclusion.fields == ("organization",)
    assert exclusion.condition.negated
    assert exclusion.condition.children == [
        ("state__in", ("credited", "failed", "refused"))
    ]
    assert MigrationExecutor(connection).loader.graph.leaf_nodes("prism") == [
        ("prism", "0084_checkout_actor_identifier")
    ]


def test_forward_upgrade_preserves_unresolved_latch_without_money_or_consent_changes(
    ledger, organization
):
    assert connection.vendor == "postgresql"
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        apps = executor.loader.project_state([BEFORE]).apps
        arming = apps.get_model("prism", "AgentccAutoReloadArming")
        receipt = apps.get_model("prism", "AgentccAutoReloadConsent")
        ledger.ManagedCreditAccount.objects.create(organization=organization)
        intent_id = f"{organization.pk}:4"
        arming.objects.create(
            organization_id=organization.pk,
            epoch=4,
            below_threshold=True,
            intent_id=intent_id,
        )
        before_accounts = list(
            ledger.ManagedCreditAccount.objects.order_by("pk").values()
        )
        before_receipts = list(receipt.objects.order_by("pk").values())
        before_arming = list(arming.objects.order_by("pk").values())
        before_balance = ledger.ledger_balance(organization).balance_micros
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        apps = executor.loader.project_state([AFTER]).apps
        intent = apps.get_model("prism", "AgentccAutoReloadIntent")
        migrated = intent.objects.get(organization_id=organization.pk)
        assert migrated.pk == intent_id
        assert migrated.epoch == 4
        assert migrated.state == "queued"  # never assume unknown prior success
        assert intent.objects.count() == 1
        assert not apps.get_model(
            "prism", "AgentccAutoReloadCreditEvent"
        ).objects.exists()
        assert (
            list(
                apps.get_model("prism", "AgentccAutoReloadArming")
                .objects.order_by("pk")
                .values()
            )
            == before_arming
        )
        assert (
            list(
                apps.get_model("prism", "AgentccAutoReloadConsent")
                .objects.order_by("pk")
                .values()
            )
            == before_receipts
        )
        assert (
            list(ledger.ManagedCreditAccount.objects.order_by("pk").values())
            == before_accounts
        )
        assert ledger.ledger_balance(organization).balance_micros == before_balance
        with pytest.raises(IntegrityError) as error, transaction.atomic():
            intent.objects.create(
                organization_id=organization.pk,
                intent_id=f"{organization.pk}:5",
                epoch=5,
            )
        assert (
            error.value.__cause__.diag.constraint_name
            == "agentcc_reload_one_nonterminal"
        )
    finally:
        MigrationExecutor(connection).migrate(restore)


def test_empty_latch_does_not_mint_an_intent_or_credit_event(organization):
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        apps = executor.loader.project_state([BEFORE]).apps
        apps.get_model("prism", "AgentccAutoReloadArming").objects.create(
            organization_id=organization.pk
        )
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        apps = executor.loader.project_state([AFTER]).apps
        assert not apps.get_model("prism", "AgentccAutoReloadIntent").objects.exists()
        assert not apps.get_model(
            "prism", "AgentccAutoReloadCreditEvent"
        ).objects.exists()
    finally:
        MigrationExecutor(connection).migrate(restore)
