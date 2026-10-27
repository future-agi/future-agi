from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0088_merge_20260627_1942"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="continuous_cursor",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
