from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0117_harness_release"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="split",
            field=models.CharField(
                blank=True,
                choices=[("train", "Train"), ("test", "Test")],
                max_length=8,
                null=True,
            ),
        ),
    ]
