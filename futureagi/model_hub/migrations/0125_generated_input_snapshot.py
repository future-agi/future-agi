from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("model_hub", "0124_dataset_saved_view")]
    operations = [
        migrations.CreateModel(
            name="GeneratedInputSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("fingerprint", models.CharField(max_length=71)),
                ("column", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.column")),
                ("row", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="model_hub.row")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("column", "row"), name="unique_generated_input_snapshot")]},
        ),
    ]
