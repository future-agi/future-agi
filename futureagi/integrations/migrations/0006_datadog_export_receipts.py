import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0029_request_log_unique_request_id"),
        ("integrations", "0005_slack_integration"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatadogExportReceipt",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("export_id", models.UUIDField(db_index=True)),
                (
                    "connection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="datadog_receipts",
                        to="integrations.integrationconnection",
                    ),
                ),
                (
                    "request_log",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="datadog_receipts",
                        to="prism.agentccrequestlog",
                    ),
                ),
            ],
            options={
                "db_table": "integrations_datadog_export_receipt",
                "constraints": [
                    models.UniqueConstraint(
                        fields=("connection", "request_log"),
                        name="uq_datadog_conn_request",
                    ),
                ],
            },
        ),
    ]
