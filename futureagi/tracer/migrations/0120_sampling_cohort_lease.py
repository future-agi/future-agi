from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0119_merge_sampling_default_and_simulation_project"),
    ]

    operations = [
        # Nullable, no default backfill: existing cohorts simply have no lease.
        migrations.AddField(
            model_name="evalsamplingcohort",
            name="lease_token",
            field=models.UUIDField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="evalsamplingcohort",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
