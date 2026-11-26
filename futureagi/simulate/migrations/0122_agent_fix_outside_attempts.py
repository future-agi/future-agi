import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0121_agent_fix_candidate_changed_files"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentfixrun",
            name="attempt_of",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="next_attempts",
                to="simulate.agentfixrun",
            ),
        ),
        migrations.AddField(
            model_name="hostedharnessjob",
            name="graded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
