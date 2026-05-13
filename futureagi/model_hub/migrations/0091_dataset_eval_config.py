import uuid

import django.contrib.postgres.fields
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("model_hub", "0090_merge_20260423_1541"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetEvalConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("deleted", models.BooleanField(default=False)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("enabled", models.BooleanField(default=True)),
                ("debounce_seconds", models.PositiveIntegerField(default=30)),
                ("max_concurrent", models.PositiveIntegerField(default=5)),
                ("column_mapping", models.JSONField(default=dict)),
                (
                    "filter_tags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=255),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                (
                    "dataset",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="eval_configs",
                        to="model_hub.dataset",
                    ),
                ),
                (
                    "eval_template",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dataset_auto_configs",
                        to="model_hub.evaltemplate",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dataset_eval_configs",
                        to="accounts.organization",
                    ),
                ),
                (
                    "workspace",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="dataset_eval_configs",
                        to="accounts.workspace",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_dataset_eval_configs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "model_hub_dataset_eval_config",
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(deleted=False),
                        fields=("dataset", "eval_template"),
                        name="unique_active_dataset_eval_config",
                    ),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="datasetevalconfig",
            index=models.Index(
                fields=["dataset", "enabled"],
                name="dataset_eval_config_dataset_enabled_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="datasetevalconfig",
            index=models.Index(
                fields=["organization"],
                name="dataset_eval_config_org_idx",
            ),
        ),
    ]
