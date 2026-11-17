import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:model_hub_dataset_version",
        "postgres:model_hub_experimentrun",
    ],
    "mitigation": (
        "Numbers model_hub_experimentrun rows whose ordinal is null 1, 2, 3 per "
        "dataset in (created_at, id) order. Reads those runs with dataset_id from "
        "model_hub_dataset_version through a server-side cursor in chunks of 1000 and"
        " writes one UPDATE per run, by primary key. It runs inside the migration's "
        "single transaction after the AddField of ordinal, so the ACCESS EXCLUSIVE "
        "lock that ADD COLUMN takes on model_hub_experimentrun is held until the "
        "whole migration commits: reads and writes of that table wait for the "
        "backfill. It is atomic, and it is recorded only after it commits (its "
        "foreign keys are deferred SQL), so an interruption leaves either nothing, "
        "and migrate runs it again, or the column, the new model_hub_dataset_baseline"
        " table and every ordinal committed but the migration not recorded, and a "
        "re-run then fails on the ordinal column that already exists. The recovery "
        "for that is migrate model_hub 0127_experimentrun_ordinal_datasetbaseline "
        "--fake, which only records it, then migrate again. Row scale is every run "
        "present when it applies; on an update from a release without "
        "model_hub_experimentrun the table is created empty in the same plan, so it "
        "is zero. The reverse is a no-op."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def assign_ordinals(apps, schema_editor):
    """Number existing runs 1, 2, 3 ... per dataset in creation order.

    The run table is new on this stack (0125), so this touches few rows.
    Soft-deleted runs are numbered too, as creation does from now on. Ties on
    created_at are broken by id.

    It reads through ``_base_manager``, which is a plain manager both on the
    historical model a migration gets and on the live model the tests pass,
    so neither the soft-delete filter nor the workspace filter of the live
    ``objects`` manager ever applies.
    """
    with executor_alias_scope(schema_editor):
        db = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        ExperimentRun = apps.get_model("model_hub", "ExperimentRun")
        runs = ExperimentRun._base_manager.using(db)
        rows = (
            runs.filter(ordinal__isnull=True)
            .order_by("dataset_version__dataset_id", "created_at", "id")
            .values_list("id", "dataset_version__dataset_id")
        )
        counters = {}
        for run_id, dataset_id in rows.iterator(chunk_size=1000):
            counters[dataset_id] = counters.get(dataset_id, 0) + 1
            runs.filter(pk=run_id).update(ordinal=counters[dataset_id])


class Migration(migrations.Migration):
    """SRV-4: run ordinals ("#N" labels) and the dataset baseline pin."""

    dependencies = [
        ("model_hub", "0126_evaltemplate_external_eval_type"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentrun",
            name="ordinal",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="DatasetBaseline",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(db_index=True, default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "dataset",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="baseline_pin",
                        to="model_hub.dataset",
                    ),
                ),
                (
                    "pinned_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "run",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="baseline_pins",
                        to="model_hub.experimentrun",
                    ),
                ),
            ],
            options={
                "db_table": "model_hub_dataset_baseline",
                "ordering": ("-created_at",),
                "abstract": False,
            },
        ),
        # After the operations above, in the same transaction. Django still
        # runs the CreateModel's foreign-key constraints as deferred SQL after
        # this backfill. That order is safe: an update of ordinal alone changes
        # no key column, so it queues no foreign-key trigger events on
        # model_hub_experimentrun, and pending trigger events are what make
        # Postgres refuse a later ALTER in the same transaction. No later
        # operation here ALTERs model_hub_experimentrun itself.
        migrations.RunPython(assign_ordinals, migrations.RunPython.noop),
    ]
