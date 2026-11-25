import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0128_base_list_current_stores")]

    operations = [
        migrations.CreateModel(
            name="MetricPgResponseRevision",
            fields=[
                ("project_id", models.UUIDField(primary_key=True, serialize=False)),
                ("incarnation", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={"db_table": "metric_pg_response_revision"},
        ),
        migrations.CreateModel(
            name="MetricPgIdentityRevision",
            fields=[
                (
                    "singleton",
                    models.PositiveSmallIntegerField(
                        default=1, primary_key=True, serialize=False
                    ),
                ),
                ("incarnation", models.UUIDField(default=uuid.uuid4, editable=False)),
                ("revision", models.PositiveBigIntegerField(default=0)),
            ],
            options={
                "db_table": "metric_pg_identity_revision",
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(singleton=1),
                        name="metric_pg_identity_singleton",
                    )
                ],
            },
        ),
    ]
