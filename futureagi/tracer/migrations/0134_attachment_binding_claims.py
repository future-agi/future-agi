"""Fair binding claims: a nullable check stamp and a per-scope claim index.

``bind_checked_at`` is written only by the binding worker when a check leaves a
row unbound; the 0133 record fence does not freeze it. Adding a nullable column
with no default is metadata-only. The partial index covers live unbound rows
only, led by scope and ordered by ``COALESCE(bind_checked_at, created_at), id``,
so each scope's claim window is one bounded index scan (LATERAL ... LIMIT).
"""

import django.db.models.functions.comparison
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0133_attachment_fences")]

    operations = [
        migrations.AddField(
            model_name="attachmentoccurrence",
            name="bind_checked_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddIndex(
            model_name="attachmentoccurrence",
            index=models.Index(
                models.F("scope"),
                django.db.models.functions.comparison.Coalesce(
                    "bind_checked_at", "created_at"
                ),
                models.F("id"),
                condition=models.Q(
                    ("bound_at__isnull", True), ("deleted_at__isnull", True)
                ),
                name="attachment_unbound_claim_idx",
            ),
        ),
    ]
