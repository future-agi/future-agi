from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0021_merge_20260526_0921"),
        ("model_hub", "0104_merge_20260526_0921"),
    ]

    operations = [
        migrations.AddField(
            model_name="optimizedataset",
            name="organization",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="optimize_datasets",
                to="accounts.organization",
            ),
        ),
        migrations.AddField(
            model_name="optimizedataset",
            name="workspace",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="optimize_datasets",
                to="accounts.workspace",
            ),
        ),
    ]
