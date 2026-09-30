from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0122_backfill_queueitem_source_preview")]

    operations = [
        migrations.AddField(
            model_name="runprompter",
            name="queued_request_id",
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
        migrations.AddField(
            model_name="runprompter",
            name="queued_row_ids",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
