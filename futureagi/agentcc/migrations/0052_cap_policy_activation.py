from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("prism", "0051_encrypt_alert_channel_urls")]

    operations = [
        migrations.CreateModel(
            name="AgentccCapActivation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("level", models.CharField(max_length=256)),
                ("activated_at", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("organization", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="accounts.organization")),
            ],
            options={"constraints": [models.UniqueConstraint(fields=("organization", "level"), name="agentcc_cap_activation_org_level")]},
        ),
    ]
