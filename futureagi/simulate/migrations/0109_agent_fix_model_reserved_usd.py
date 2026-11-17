from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0108_merge_c26_dev_1109"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentfixrun",
            name="model_reserved_usd",
            field=models.DecimalField(max_digits=10, decimal_places=4, default=0),
        ),
    ]
