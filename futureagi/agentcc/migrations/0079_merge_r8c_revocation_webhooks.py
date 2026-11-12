"""Join historical key revocations and the webhook credential merge leaf."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0078_historical_key_revocations"),
        ("prism", "0078_merge_w11_webhook_credentials"),
    ]

    operations = []
