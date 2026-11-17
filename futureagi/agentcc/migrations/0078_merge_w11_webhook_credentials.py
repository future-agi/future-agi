"""Join approval lifecycle and webhook credential storage without new operations."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0075_w11_approval_lifecycle"),
        ("prism", "0077_remove_webhook_plaintext"),
    ]

    operations = []
