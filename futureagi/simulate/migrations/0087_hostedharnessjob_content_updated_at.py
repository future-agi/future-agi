from django.db import migrations, models
from django.db.models import F
from django.db.models.functions import Coalesce


def backfill_content_updated_at(apps, schema_editor):
    """Seed the new column from the best evidence each existing row carries.

    A terminal job last changed when it finished; anything else has no record of
    a meaningful change beyond its creation, and ``updated_at`` cannot stand in
    because a poll tick moves it.
    """
    HostedHarnessJob = apps.get_model("simulate", "HostedHarnessJob")
    HostedHarnessJob.objects.filter(content_updated_at__isnull=True).update(
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
