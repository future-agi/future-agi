from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0135_eval_put_back")]

    operations = [
        migrations.AddField(
            model_name="evaltask",
            name="failure_reason",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
