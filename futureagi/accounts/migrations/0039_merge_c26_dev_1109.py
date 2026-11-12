from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0038_organizationmembership_removed_at"),
        ("accounts", "0031_orgapikey_coding_agent"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orgapikey",
            name="type",
            field=models.CharField(
                max_length=50,
                default="user",
                choices=[("user", "User"), ("coding_agent", "Coding agent")],
            ),
        ),
    ]
