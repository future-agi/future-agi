"""Add project-scoped tag definitions.

Assignments stay in ClickHouse ``spans.tags``. This table holds the reusable
name and colour the UI edits once per project.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0114_backfill_group_redirects"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProjectTagDefinition",
            fields=[
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "deleted",
                    models.BooleanField(db_index=True, default=False),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "id",
                    models.BigAutoField(primary_key=True, serialize=False),
                ),
                ("name", models.CharField(max_length=255)),
                ("color", models.CharField(blank=True, default="", max_length=7)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="tag_definitions",
                        to="tracer.project",
                    ),
                ),
            ],
            options={
                "db_table": "tracer_project_tag_definition",
                "indexes": [
                    models.Index(
                        fields=["project", "name"],
                        name="tracer_proj_project_6a0b0d_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(deleted=False),
                        fields=("project", "name"),
                        name="unique_project_tag_name",
                    )
                ],
            },
        ),
    ]
