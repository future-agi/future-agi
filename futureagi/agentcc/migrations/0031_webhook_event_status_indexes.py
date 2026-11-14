import django.contrib.postgres.operations
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and avoids the
    # write-blocking lock a plain CREATE INDEX would take on a large event table.
    atomic = False

    dependencies = [
        ("prism", "0030_webhook_delivery_health"),
    ]

    operations = [
        django.contrib.postgres.operations.AddIndexConcurrently(
            model_name="agentccwebhookevent",
            index=models.Index(
                fields=["webhook", "status", "created_at"],
                name="agentcc_whevt_wh_status_ts",
            ),
        ),
        django.contrib.postgres.operations.AddIndexConcurrently(
            model_name="agentccwebhookevent",
            index=models.Index(
                fields=["status", "created_at"],
                name="agentcc_whevt_status_ts",
            ),
        ),
    ]
