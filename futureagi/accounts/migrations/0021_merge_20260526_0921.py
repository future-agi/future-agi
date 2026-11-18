from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0020_reseed_broken_demo_data"),
        ("accounts", "0020_user_last_timezone"),
    ]

    operations = []
