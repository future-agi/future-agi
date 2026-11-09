# Choices only: no schema change. Password-reset links get their own
# AuthToken type so they can never be presented as a login session, nor a
# login session as a reset link.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0035_gateway_retirement_fence"),
    ]

    operations = [
        migrations.AlterField(
            model_name="authtoken",
            name="auth_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("access", "Access"),
                    ("refresh", "Refresh"),
                    ("password_reset", "Password Reset"),
                ],
                max_length=255,
                null=True,
            ),
        ),
    ]
