# Omega Hub operations: the stall alert ages the queue from when a job last entered it, and approved
# bundles record the object-lock date their renewal set (services/bundle_locks.py).

import django.utils.timezone
from django.db import DEFAULT_DB_ALIAS, migrations, models
from django.db.models import F

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:omega_hub_check_job"],
    "mitigation": (
        "queued_since_created sets queued_at to created_at on every "
        "omega_hub_check_job row with one UPDATE, inside the migration's transaction, "
        "right after this migration adds queued_at: a job created before it has no "
        "requeue time, so its creation time stands in. It reads and writes no other "
        "table. A failed run rolls back whole, and a retry writes the same values. "
        "Run again after the app has requeued jobs, it would move their queued_at "
        "back to created_at, which only makes the stall alert see those jobs as "
        "older. The reverse is a no-op; the column goes with the field. The index on "
        "status and queued_at in the same migration is built without CONCURRENTLY on "
        "this table. Row scale: one row per check job, a few per Hub submission; not "
        "a registered high-volume table, so the single UPDATE and the index build are "
        "short."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def queued_since_created(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # Jobs that exist before this migration have no requeue time; their creation is the best one.
        db = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        CheckJob = apps.get_model("omega_hub", "CheckJob")
        CheckJob.objects.using(db).update(queued_at=F("created_at"))


class Migration(migrations.Migration):

    dependencies = [
        ("omega_hub", "0003_hub_operations"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkjob",
            name="queued_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.RunPython(queued_since_created, migrations.RunPython.noop),
        migrations.AddIndex(
            model_name="checkjob",
            index=models.Index(fields=["status", "queued_at"], name="omega_hub_job_queued_idx"),
        ),
        migrations.AddField(
            model_name="version",
            name="bundle_locked_until",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
