# Generated manually on 2026-05-13

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0096_queueitemreviewcomment_mentions"),
    ]

    operations = [
        migrations.AddField(
            model_name="queueitemreviewcomment",
            name="reactions",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
