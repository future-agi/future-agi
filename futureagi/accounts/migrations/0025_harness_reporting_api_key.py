from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_sosloginproxy"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orgapikey",
            name="type",
            field=models.CharField(
                choices=[
                    ("system", "System"),
                    ("user", "User"),
                    ("mcp", "MCP"),
                    ("harness", "Harness reporting"),
                ],
                default="system",
                max_length=50,
            ),
        ),
        migrations.AddConstraint(
            model_name="orgapikey",
            constraint=models.UniqueConstraint(
                condition=models.Q(type="harness", deleted=False),
                fields=("organization", "type"),
                name="unique_harness_api_key_per_org_not_deleted",
                violation_error_message=(
                    "Only one harness API key is allowed per organization."
                ),
            ),
        ),
    ]
