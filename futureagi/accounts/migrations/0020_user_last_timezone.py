from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_merge_20260407_1927"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="last_timezone",
            field=models.CharField(
                blank=True,
                default="UTC",
                help_text="Last known IANA timezone (from browser Intl API)",
                max_length=64,
            ),
        ),
    ]
