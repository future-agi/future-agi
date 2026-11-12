"""Forward intent exclusion; preserve existing latches, never backfill money."""

import django.db.models.deletion
from django.db import migrations, models


def preserve_latched_intents(apps, schema_editor):
    """An r4 latch is unresolved, not permission to start a second checkout.

    This copies gateway metadata only. Historical real outbox reconciliation is
    main-owned: no terminal state, receipt, balance or credit is inferred here.
    """
    db = schema_editor.connection.alias
    states = apps.get_model("prism", "AgentccAutoReloadArming")
    intents = apps.get_model("prism", "AgentccAutoReloadIntent")
    for state in states.objects.using(db).exclude(intent_id="").iterator():
        if state.intent_id != f"{state.organization_id}:{state.epoch}":
            raise RuntimeError("auto-reload legacy intent requires reconciliation")
        intents.objects.using(db).create(
            organization_id=state.organization_id,
            intent_id=state.intent_id,
            epoch=state.epoch,
            state="queued",
        )


class Migration(migrations.Migration):
    dependencies = [("prism", "0080_auto_reload_arming")]

    operations = [
        migrations.CreateModel(
            name="AgentccAutoReloadIntent",
            fields=[
                (
                    "intent_id",
                    models.CharField(max_length=64, primary_key=True, serialize=False),
                ),
                ("epoch", models.PositiveBigIntegerField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            (value, value)
                            for value in (
                                "queued",
                                "pending_reconcile",
                                "credited",
                                "failed",
                                "refused",
                            )
                        ],
                        default="queued",
                        max_length=24,
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
            ],
            options={"db_table": "agentcc_auto_reload_intent"},
        ),
        migrations.AddConstraint(
            model_name="agentccautoreloadintent",
            constraint=models.UniqueConstraint(
                fields=("organization",),
                condition=~models.Q(state__in=("credited", "failed", "refused")),
                name="agentcc_reload_one_nonterminal",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentccautoreloadintent",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    state__in=(
                        "queued",
                        "pending_reconcile",
                        "credited",
                        "failed",
                        "refused",
                    )
                ),
                name="agentcc_reload_known_state",
            ),
        ),
        migrations.CreateModel(
            name="AgentccAutoReloadCreditEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("ledger_entry_id", models.CharField(max_length=255)),
                ("before_micros", models.BigIntegerField()),
                ("after_micros", models.BigIntegerField()),
                ("threshold_micros", models.BigIntegerField()),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        to="accounts.organization",
                    ),
                ),
            ],
            options={"db_table": "agentcc_auto_reload_credit_event"},
        ),
        migrations.AddConstraint(
            model_name="agentccautoreloadcreditevent",
            constraint=models.UniqueConstraint(
                fields=("organization", "ledger_entry_id"),
                name="agentcc_reload_credit_once",
            ),
        ),
        migrations.AddConstraint(
            model_name="agentccautoreloadcreditevent",
            constraint=models.CheckConstraint(
                condition=models.Q(after_micros__gt=models.F("before_micros")),
                name="agentcc_reload_positive_credit",
            ),
        ),
        migrations.RunPython(preserve_latched_intents, migrations.RunPython.noop),
    ]
