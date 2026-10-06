from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0109_simulation_debug_analysis"),
    ]

    operations = [
        migrations.AlterField(
            model_name="traceinvestigationrequirementcheck",
            name="requirement_id",
            field=models.CharField(max_length=256),
        ),
    ]
