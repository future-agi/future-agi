from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and avoids the
    # write-blocking lock a plain CREATE INDEX would take on usage_apicalllog.
    # Partial: only rows still in processing (tens of thousands) are indexed.
    atomic = False

    dependencies = [
        ("usage", "0029_merge_20260806_1623"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="apicalllog",
            index=models.Index(
                condition=models.Q(status="processing"),
                fields=["created_at"],
                name="idx_apicalllog_processing",
            ),
        ),
    ]
