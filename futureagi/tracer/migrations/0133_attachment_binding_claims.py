"""Fair binding claims: a nullable check stamp and a partial index on unbound rows.

``bind_checked_at`` is written only by the binding worker when a check leaves a
row unbound; the 0132 record fence does not freeze it. Adding a nullable column
with no default is metadata-only, and the index covers unbound rows only.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("tracer", "0132_attachment_fences")]

    operations = [
        migrations.AddField(
            model_name="attachmentoccurrence",
            name="bind_checked_at",
            field=models.DateTimeField(null=True),
        ),
        migrations.AddIndex(
            model_name="attachmentoccurrence",
            index=models.Index(
                condition=models.Q(("bound_at__isnull", True)),
                fields=["created_at", "id"],
                name="attachment_unbound_claim_idx",
            ),
        ),
    ]
