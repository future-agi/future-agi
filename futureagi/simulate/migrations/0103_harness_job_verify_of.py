import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0102_agent_fix_engine_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="verify_of",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="verify_runs",
                to="simulate.hostedharnessjob",
            ),
        ),
        migrations.AddConstraint(
            model_name="hostedharnessjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("verify_of__isnull", False))
                & ~models.Q(("state__in", ["failed", "canceled"])),
                fields=("verify_of", "agent_version"),
                name="uniq_harness_verify_agent_version",
            ),
        ),
    ]
