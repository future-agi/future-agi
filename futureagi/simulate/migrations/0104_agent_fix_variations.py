import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0103_harness_job_verify_of"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentfixrun",
            name="suite_version",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="simulate.harnesssuiteversion",
            ),
        ),
        migrations.AddField(
            model_name="harnesssuiteversion",
            name="fix_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="+",
                to="simulate.agentfixrun",
            ),
        ),
        migrations.AlterField(
            model_name="agentfixrun",
            name="state",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("running", "Running"),
                    ("stopping", "Stopping"),
                    ("ready", "Ready for review"),
                    ("no_improvement", "No safe improvement"),
                    ("not_reproduced", "Could not reproduce"),
                    ("out_of_budget", "Stopped at budget"),
                    ("stopped", "Stopped"),
                    ("failed", "Failed"),
                    ("too_few_cases", "Too few failing cases"),
                ],
                default="queued",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="harnessenvironmentversion",
            name="origin",
            field=models.CharField(
                choices=[
                    ("authoring", "Authoring"),
                    ("nudge", "Nudge"),
                    ("manual", "Manual"),
                    ("chat", "Chat"),
                    ("backfill", "Backfill"),
                    ("extend", "Extend"),
                    ("fix", "Fix"),
                ],
                max_length=24,
            ),
        ),
        migrations.AlterField(
            model_name="harnesssuiteversion",
            name="origin",
            field=models.CharField(
                choices=[
                    ("authoring", "Authoring"),
                    ("nudge", "Nudge"),
                    ("manual", "Manual"),
                    ("chat", "Chat"),
                    ("backfill", "Backfill"),
                    ("extend", "Extend"),
                    ("fix", "Fix"),
                ],
                max_length=24,
            ),
        ),
    ]
