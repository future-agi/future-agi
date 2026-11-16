import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0105_harness_job_fix_eval_cost"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="graded_for",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="adopted_check_runs",
                to="simulate.agentfixrun",
            ),
        ),
    ]
