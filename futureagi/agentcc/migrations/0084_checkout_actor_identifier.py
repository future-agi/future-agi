"""Retain the initiating actor without inventing identities for old null rows."""

from django.db import migrations, models
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied


def copy_existing_actor(apps, schema_editor):
    binding = apps.get_model("prism", "AgentccCheckoutBinding")
    binding.objects.using(schema_editor.connection.alias).filter(
        actor_identifier__isnull=True,
        created_by_id__isnull=False,
    ).update(actor_identifier=models.F("created_by_id"))


class Migration(migrations.Migration):
    # Join the revocation/webhook merge leaf (0079_merge_r8c_revocation_webhooks) so
    # the agentcc app keeps exactly one leaf: this migration. Operations unchanged.
    dependencies = [
        ("prism", "0083_managed_credit_checkout_binding"),
        ("prism", "0079_merge_r8c_revocation_webhooks"),
    ]
    # c2.5 recorded this as prism.0080_checkout_actor_identifier.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('prism', '0080_checkout_actor_identifier'), operations=[
        migrations.AddField(
            model_name="agentcccheckoutbinding",
            name="actor_identifier",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(copy_existing_actor, migrations.RunPython.noop),
    ])]
