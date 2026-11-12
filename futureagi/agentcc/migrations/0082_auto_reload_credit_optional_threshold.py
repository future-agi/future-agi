"""Receipt credits with no configured threshold without changing intent exclusion."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0081_auto_reload_intent_exclusion")]

    operations = [
        migrations.AlterField(
            model_name="agentccautoreloadcreditevent",
            name="threshold_micros",
            field=models.BigIntegerField(null=True),
        ),
    ]
