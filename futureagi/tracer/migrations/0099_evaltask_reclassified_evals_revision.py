from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0098_observabilityprovider_poll_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="reclassified_evals_revision",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
    ]
