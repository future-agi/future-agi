from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("model_hub", "0124_dataset_description_tags"),
    ]

    operations = [
        migrations.CreateModel(
            name="DatasetStar",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("dataset", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="stars", to="model_hub.dataset")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dataset_stars", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "constraints": [models.UniqueConstraint(fields=("user", "dataset"), name="unique_personal_dataset_star")],
            },
        ),
    ]
