from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("simulate", "0104_agent_fix_variations"),
    ]

    operations = [
        migrations.AddField(
            model_name="hostedharnessjob",
            name="fix_eval_cost_usd",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=10),
        ),
    ]
