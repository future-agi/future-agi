from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0119_retire_deleted_eval_memberships"),
    ]

    operations = [
        migrations.AddField(
            model_name="errorfeedalertissuestate",
            name="occurrences_high_water",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
