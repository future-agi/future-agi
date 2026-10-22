import django.contrib.postgres.indexes
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations


class Migration(migrations.Migration):
    # CREATE INDEX CONCURRENTLY can't run inside a transaction, and avoids the
    # write-blocking lock a plain CREATE INDEX would take on the request log.
    atomic = False

    dependencies = [
        ("prism", "0026_clear_stuck_ui_guardrails_encrypted_configs"),
    ]

    operations = [
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["metadata"],
                name="agentcc_req_metadata_gin",
                opclasses=["jsonb_path_ops"],
            ),
        ),
    ]
