"""Forward nullable-threshold migration; run in a disposable PostgreSQL DB."""

import importlib

import pytest
from django.db import IntegrityError, connection, migrations, transaction
from django.db.migrations.executor import MigrationExecutor

pytestmark = [pytest.mark.integration, pytest.mark.django_db(transaction=True)]
BEFORE = ("prism", "0081_auto_reload_intent_exclusion")
AFTER = ("prism", "0082_auto_reload_credit_optional_threshold")


def test_optional_threshold_is_one_forward_alter_without_data_rewrite():
    migration = importlib.import_module(
        "agentcc.migrations.0082_auto_reload_credit_optional_threshold"
    ).Migration
    assert migration.dependencies == [BEFORE]
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.AlterField)
    assert operation.model_name == "agentccautoreloadcreditevent"
    assert operation.name == "threshold_micros"
    assert operation.field.null
    assert MigrationExecutor(connection).loader.graph.leaf_nodes("prism") == [
        ("prism", "0084_checkout_actor_identifier")
    ]


def test_optional_threshold_upgrade_preserves_receipts_and_exclusion(organization):
    assert connection.vendor == "postgresql"
    executor = MigrationExecutor(connection)
    restore = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([BEFORE])
        apps = executor.loader.project_state([BEFORE]).apps
        events = apps.get_model("prism", "AgentccAutoReloadCreditEvent")
        intents = apps.get_model("prism", "AgentccAutoReloadIntent")
        arming = apps.get_model("prism", "AgentccAutoReloadArming")
        events.objects.create(
            organization_id=organization.pk,
            ledger_entry_id="configured-before-upgrade",
            before_micros=1,
            after_micros=3,
            threshold_micros=2,
        )
        intent_id = f"{organization.pk}:4"
        intents.objects.create(
            organization_id=organization.pk,
            intent_id=intent_id,
            epoch=4,
            state="pending_reconcile",
        )
        arming.objects.create(
            organization_id=organization.pk,
            epoch=4,
            below_threshold=True,
            intent_id=intent_id,
        )
        before_events = list(events.objects.values())
        before_intents = list(intents.objects.values())
        before_arming = list(arming.objects.values())
        executor = MigrationExecutor(connection)
        executor.migrate([AFTER])
        apps = executor.loader.project_state([AFTER]).apps
        events = apps.get_model("prism", "AgentccAutoReloadCreditEvent")
        intents = apps.get_model("prism", "AgentccAutoReloadIntent")
        assert list(events.objects.values()) == before_events
        assert list(intents.objects.values()) == before_intents
        assert (
            list(apps.get_model("prism", "AgentccAutoReloadArming").objects.values())
            == before_arming
        )
        assert events._meta.get_field("threshold_micros").null
        assert not events._meta.get_field("before_micros").null
        assert not events._meta.get_field("after_micros").null
        values = {
            "organization_id": organization.pk,
            "ledger_entry_id": "disabled-after-upgrade",
            "before_micros": 9_000_000,
            "after_micros": 34_000_000,
            "threshold_micros": None,
        }
        event = events.objects.create(**values)
        assert events.objects.get(pk=event.pk).threshold_micros is None
        with pytest.raises(IntegrityError) as error, transaction.atomic():
            events.objects.create(**values)
        assert (
            error.value.__cause__.diag.constraint_name == "agentcc_reload_credit_once"
        )
        with pytest.raises(IntegrityError) as error, transaction.atomic():
            events.objects.create(
                **{**values, "ledger_entry_id": "not-positive", "after_micros": 0}
            )
        assert (
            error.value.__cause__.diag.constraint_name
            == "agentcc_reload_positive_credit"
        )
        for field in ("before_micros", "after_micros"):
            with pytest.raises(IntegrityError), transaction.atomic():
                events.objects.create(
                    **{**values, "ledger_entry_id": f"null-{field}", field: None}
                )
        with pytest.raises(IntegrityError) as error, transaction.atomic():
            intents.objects.create(
                organization_id=organization.pk,
                intent_id=f"{organization.pk}:5",
                epoch=5,
            )
        assert (
            error.value.__cause__.diag.constraint_name
            == "agentcc_reload_one_nonterminal"
        )
        assert list(intents.objects.values()) == before_intents
        assert events.objects.count() == 2
    finally:
        MigrationExecutor(connection).migrate(restore)
