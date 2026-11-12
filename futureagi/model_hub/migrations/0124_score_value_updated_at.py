from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0123_usereval_is_experiment_default")]
    operations = [
        migrations.AddField(
            model_name="score",
            name="value_updated_at",
            field=models.DateTimeField(null=True, editable=False),
        ),
        migrations.AlterField(
            model_name="score",
            name="value_updated_at",
            field=models.DateTimeField(null=True, default=timezone.now, editable=False),
        ),
    ]
