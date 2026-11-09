# Merges the two parallel 0002 migrations of the webhooks app (wave 2 integration): WH-2's 0002_api (the API ledger)
# and WH-1b's 0002_operations (state-row rate and health, ops alerts and snapshots). They touch no common model, so
# the merge has no operations; `makemigrations webhooks --check` proves the graph has one leaf and no pending change.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("webhooks", "0002_api"),
        ("webhooks", "0002_operations"),
    ]

    operations = []
