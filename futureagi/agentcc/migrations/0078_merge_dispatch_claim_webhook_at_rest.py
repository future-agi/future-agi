"""Join the dispatch-claim and webhook-credential branches for c2.5 candidate r2.

Both branches start from 0074_api_key_provisioning_state and touch unrelated
tables. This graph join adds no operations and does not reorder either chain.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("prism", "0075_dispatch_claim"),
        ("prism", "0077_remove_webhook_plaintext"),
    ]
    operations = []
