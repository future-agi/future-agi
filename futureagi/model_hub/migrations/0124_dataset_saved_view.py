import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0123_usereval_is_experiment_default"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetSavedView",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=120)),
                ("state", models.JSONField(default=dict)),
                ("shared", models.BooleanField(default=False)),
                ("deleted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ("dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="saved_grid_views", to="model_hub.dataset")),
            ],
            options={
                "db_table": "model_hub_dataset_saved_view",
                "ordering": ["created_at", "id"],
                "constraints": [models.UniqueConstraint(condition=models.Q(deleted=False), fields=("dataset", "created_by", "name"), name="unique_dataset_view_name_per_user")],
            },
        ),
    ]
