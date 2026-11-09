import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0124_dataset_saved_view")]

    operations = [
        migrations.CreateModel(
            name="DatasetSplit",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=30)),
                ("color", models.CharField(default="info", max_length=20)),
                ("description", models.CharField(blank=True, default="", max_length=500)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="splits", to="model_hub.dataset")),
            ],
            options={
                "ordering": ["name", "id"],
                "constraints": [models.UniqueConstraint(fields=("dataset", "name"), name="dataset_split_name_unique")],
            },
        ),
        migrations.CreateModel(
            name="DatasetSplitMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("split", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="model_hub.datasetsplit")),
                ("row", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="split_memberships", to="model_hub.row")),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("split", "row"), name="dataset_split_row_unique")],
            },
        ),
    ]
