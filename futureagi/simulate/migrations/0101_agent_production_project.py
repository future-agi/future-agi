import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0100_code_archive"),
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentdefinition",
            name="production_project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="production_agents",
                to="tracer.project",
            ),
        ),
    ]
