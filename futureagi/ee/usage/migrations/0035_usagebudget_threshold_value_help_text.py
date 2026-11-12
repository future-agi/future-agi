from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("usage", "0034_merge_c26_dev_candidate"),
    ]

    operations = [
        migrations.AlterField(
            model_name="usagebudget",
            name="threshold_value",
            field=models.DecimalField(
                decimal_places=4,
                help_text="In display units (credits, GB) or US dollars for total_spend.",
                max_digits=16,
            ),
        ),
    ]
