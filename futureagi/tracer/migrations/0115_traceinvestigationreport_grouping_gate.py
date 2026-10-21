from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("tracer", "0114_backfill_group_redirects"),
    ]

    operations = [
        # Nullable with no default: existing reports keep the legacy gate, and
        # pods still on the previous release can insert reports during rollout.
        migrations.AddField(
            model_name="traceinvestigationreport",
            name="grouping_gate",
            field=models.CharField(blank=True, max_length=32, null=True),
        ),
    ]
