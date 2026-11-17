from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("model_hub", "0145_dataset_metadata"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetVersionTag",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=30)),
                ("note", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="version_tags",
                        to="model_hub.dataset",
                    ),
                ),
                (
                    "version",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="tags",
                        to="model_hub.datasetversion",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "constraints": [
                    models.UniqueConstraint(
                        fields=("dataset", "name"), name="unique_dataset_version_tag"
                    ),
                ],
            },
        ),
    ]
