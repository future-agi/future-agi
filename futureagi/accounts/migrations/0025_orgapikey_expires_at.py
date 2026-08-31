from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0024_sosloginproxy"),
    ]

    operations = [
        migrations.AddField(
            model_name="orgapikey",
            name="expires_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When this key stops authenticating. Null means it never expires.",
                null=True,
            ),
        ),
    ]
