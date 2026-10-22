import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0097_merge_selected_runs_environment_v3"),
        ("tracer", "0114_backfill_group_redirects"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="project",
            options={"default_manager_name": "objects", "ordering": ["-updated_at"]},
        ),
        migrations.AddField(
            model_name="project",
            name="simulation_agent",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="simulation_project",
                to="simulate.agentdefinition",
            ),
        ),
    ]
