import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0124_agent_fix_outside_attempts"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="split",
            field=models.CharField(
                blank=True,
                choices=[("train", "Train"), ("test", "Test")],
                max_length=8,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="hostedharnessjob",
            name="locked_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="+",
                to="simulate.harnesslockedversion",
            ),
        ),
    ]
