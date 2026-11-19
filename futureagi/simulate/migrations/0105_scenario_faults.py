from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0104_actors"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessscenario",
            name="faults",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
