"""Support all three eligible branches of the bounded derivative work page."""

from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["index"],
    "tables": [
        "postgres:tracer_attachmentrecordingderivative",
        "postgres:pg_index",
        "postgres:django_migrations",
    ],
    "mitigation": "Build a partial queue index concurrently; leave the existing production and cleanup indexes in place.",
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0145_attachment_import_schedule")]
    operations = [
        AddIndexConcurrently(
            model_name="attachmentrecordingderivative",
            index=models.Index(
                fields=["available_at", "id"],
                condition=models.Q(
                    state__in=["queued", "recovering", "writing", "cleanup", "failed"]
                )
                | models.Q(state="ready", unowned_cleanup_due_at__isnull=False),
                name="att_derivative_dispatch_due",
            ),
        ),
    ]
