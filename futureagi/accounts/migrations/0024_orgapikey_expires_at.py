from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0023_create_missing_org_memberships"),
    ]

    operations = [
        migrations.AddField(
            model_name="orgapikey",
            name="expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="Optional expiry timestamp. Null means the key never expires.",
                null=True,
            ),
        ),
    ]
