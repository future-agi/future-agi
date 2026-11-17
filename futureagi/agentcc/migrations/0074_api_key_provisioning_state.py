from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prism", "0073_merge_708j_round3")]

    operations = [
        migrations.AlterField(
            model_name="agentccapikey",
            name="status",
            field=models.CharField(
                max_length=20,
                default="active",
                choices=[
                    ("active", "Active"), ("revoked", "Revoked"),
                    ("expired", "Expired"), ("pending", "Pending"),
                    ("failed", "Failed"),
                ],
            ),
        ),
    ]
