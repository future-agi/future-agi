"""Create an empty scheduling table; historical owner rows remain untouched."""

from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["constraint", "not_null"],
    "tables": [
        "postgres:tracer_attachmentimportschedule",
        "postgres:tracer_attachmentimportjob",
        "postgres:django_migrations",
    ],
    "mitigation": "Create an empty table referencing existing immutable job IDs. No backfill, trigger changes, lease transitions, or reservation writes.",
    "reviewer": "nik13",
    "review": "https://github.com/future-agi/future-agi/pull/3943#discussion_r4657808637",
    "large_volume_rehearsal": True,
}


class Migration(migrations.Migration):
    dependencies = [("tracer", "0144_attachment_completed_import_binding")]
    operations = [
        migrations.CreateModel(
            name="AttachmentImportSchedule",
            fields=[
                (
                    "job",
                    models.OneToOneField(
                        on_delete=models.CASCADE,
                        primary_key=True,
                        related_name="schedule",
                        serialize=False,
                        to="tracer.attachmentimportjob",
                    ),
                ),
                ("retry_at", models.DateTimeField()),
            ],
        ),
    ]
