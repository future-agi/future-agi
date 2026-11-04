from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("prism", "0029_request_log_unique_request_id")]

    operations = [
        AddIndexConcurrently(
            model_name="agentccrequestlog",
            index=models.Index(
                fields=["organization", "workspace", "created_at", "id"],
                condition=models.Q(deleted=False),
                name="agentcc_req_export_scan",
            ),
        ),
    ]
