from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mcp_server", "0005_oauth_refresh_token_families"),
    ]

    operations = [
        migrations.AddField(
            model_name="mcpoauthrefreshfamily",
            name="scope",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
