import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0028_merge_authtoken_scope_and_simulation_agent_key"),
        ("model_hub", "0125_dataset_stars"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetFolder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("path", models.CharField(max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.workspace")),
            ],
            options={
                "ordering": ["path"],
                "constraints": [models.UniqueConstraint(fields=("organization", "workspace", "path"), name="unique_dataset_folder_path")],
            },
        ),
        migrations.AddField(
            model_name="dataset",
            name="folder",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="datasets", to="model_hub.datasetfolder"),
        ),
    ]
