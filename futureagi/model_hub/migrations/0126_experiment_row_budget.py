import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0125_experiment_run_policy")]

    operations = [
        migrations.CreateModel(
            name="ExperimentRowBudget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("variant", models.CharField(max_length=100)),
                ("graph_execution_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("limits", models.JSONField(default=dict)),
                ("usage", models.JSONField(default=dict)),
                ("reservations", models.JSONField(default=dict)),
                ("deadline", models.DateTimeField(blank=True, null=True)),
                ("exhausted", models.BooleanField(default=False)),
                ("experiment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.experimentstable")),
                ("row", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.row")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("experiment", "row", "variant"), name="experiment_row_budget_variant")]},
        ),
    ]
