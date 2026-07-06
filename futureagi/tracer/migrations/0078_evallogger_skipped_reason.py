from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0077_merge_20260514_1559"),
    ]

    operations = [
        migrations.AddField(
            model_name="evallogger",
            name="skipped_reason",
            field=models.TextField(blank=True, null=True),
        ),
    ]
