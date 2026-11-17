"""The workspace tag registry and experiment tags: two new tables, no data."""

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0042_merge_line_and_dev_1fc7d403"),
        ("model_hub", "0143_row_activity"),
    ]

    operations = [
        migrations.CreateModel(
            name="WorkspaceTag",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=64)),
                ("name_key", models.CharField(max_length=64)),
                ("color", models.CharField(max_length=16)),
                ("description", models.TextField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.organization")),
                ("workspace", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="accounts.workspace")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "model_hub_workspace_tag",
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("workspace", "name_key"), name="workspace_tag_name_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="ExperimentTag",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("tag", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="experiment_links", to="model_hub.workspacetag")),
                ("experiment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tag_links", to="model_hub.experimentstable")),
            ],
            options={
                "db_table": "model_hub_experiment_tag",
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.UniqueConstraint(fields=("experiment", "tag"), name="experiment_tag_unique"),
                ],
            },
        ),
    ]
