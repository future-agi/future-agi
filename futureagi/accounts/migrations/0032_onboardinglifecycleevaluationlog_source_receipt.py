import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0031_onboardingactivationfactreceipt_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="onboardinglifecycleevaluationlog",
            name="source_receipt",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="lifecycle_evaluation",
                to="accounts.onboardingactivationfactreceipt",
            ),
        ),
    ]
