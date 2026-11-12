from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("mcp_server", "0005_personal_key_reconnect"),
    ]

    operations = [
        migrations.CreateModel(
            name="MCPOAuthAccessRevocation",
            fields=[
                (
                    "token_digest",
                    models.CharField(
                        max_length=64, primary_key=True, serialize=False
                    ),
                ),
                ("expires_at", models.DateTimeField(db_index=True)),
            ],
        ),
    ]
