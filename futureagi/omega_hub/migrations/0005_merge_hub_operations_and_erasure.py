# Integration round 4: HUB-LAUNCH-SAFETY's 0003 (erasure, email verification) and HUB-OPS's 0003-0004
# (operations, queue age, bundle locks) both follow 0002. They touch different models, so this merge
# migration only joins the two leaves; it changes nothing.

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("omega_hub", "0003_erasure_and_email_verification"),
        ("omega_hub", "0004_queue_age_and_bundle_locks"),
    ]

    operations = []
