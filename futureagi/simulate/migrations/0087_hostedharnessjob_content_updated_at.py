from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Coalesce

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_hosted_harness_job"],
    "mitigation": (
        "forward adds a nullable content_updated_at column to "
        "simulate_hosted_harness_job, then fills it in one set-based UPDATE on the "
        "migration's own connection, from terminal_at or, when that is NULL, "
        "created_at, on rows where it is NULL. A database that applied 1.58.0 already "
        "has this migration recorded and does not run it again. On a fresh install "
        "simulate_hosted_harness_job has no rows when it runs, so nothing is written. "
        "Safe to run again: the UPDATE touches only rows still NULL, and the migration "
        "is atomic, so a failed run rolls back whole. The data step's reverse is a "
        "no-op (elidable); reversing drops the column. Row scale: one UPDATE over the "
        "hosted harness jobs; not a registered high-volume table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def backfill_content_updated_at(apps, schema_editor):
    """Seed the new column from the best evidence each existing row carries.

    A terminal job last changed when it finished; anything else has no record of
    a meaningful change beyond its creation, and ``updated_at`` cannot stand in
    because a poll tick moves it.
    """
    HostedHarnessJob = apps.get_model("simulate", "HostedHarnessJob")
    HostedHarnessJob.objects.using(schema_editor.connection.alias).filter(content_updated_at__isnull=True).update(
        content_updated_at=Coalesce(F("terminal_at"), F("created_at"))
    )


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0086_hostedharnessattempt_diagnostics"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="content_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            backfill_content_updated_at, migrations.RunPython.noop, elidable=True
        ),
    ]
