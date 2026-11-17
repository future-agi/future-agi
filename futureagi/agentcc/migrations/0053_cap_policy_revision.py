from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("prism", "0052_cap_policy_activation")]

    operations = [
        migrations.CreateModel(
            name="AgentccCapPolicyRevision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("revision", models.PositiveBigIntegerField(default=0)),
                ("organization", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
            ],
        ),
    ]
