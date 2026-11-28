from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0126_skills_backend_integration")]

    operations = [
        migrations.AlterField(
            model_name="hubskillspendpolicy",
            name="period_limit_usd",
            field=models.DecimalField(decimal_places=8, max_digits=18, null=True),
        ),
        migrations.AddConstraint(
            model_name="hubskillspendpolicy",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(revision=0, enabled=True, period_limit_usd__isnull=True)
                    | models.Q(revision__gt=0, period_limit_usd__isnull=False)
                ),
                name="hub_skill_spend_policy_mode",
            ),
        ),
    ]
