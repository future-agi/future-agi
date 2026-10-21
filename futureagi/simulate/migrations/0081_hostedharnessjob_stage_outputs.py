from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("simulate", "0080_merge_20260825_1857")]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="stage_outputs",
            field=models.JSONField(blank=True, default=list),
        )
    ]
