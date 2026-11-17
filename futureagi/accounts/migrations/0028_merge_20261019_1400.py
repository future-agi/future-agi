# Joins two accounts branches merged into omega/integration on 2026-10-19: the FA -> EE
# revocation outbox (0026_host_event_outbox, READINESS lane 1.2a) and the sealed API
# secrets (0026_orgapikey_secret_hash, 0027_seal_api_secrets, READINESS L03). They touch
# different tables, so either order is valid; this node only gives the app one leaf.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0026_host_event_outbox"),
        ("accounts", "0027_seal_api_secrets"),
    ]

    operations = []
