"""Retain the initiating actor without inventing identities for old null rows."""

from django.db import migrations, models


def copy_existing_actor(apps, schema_editor):
    binding = apps.get_model("prism", "AgentccCheckoutBinding")
    binding.objects.using(schema_editor.connection.alias).filter(
        actor_identifier__isnull=True,
        created_by_id__isnull=False,
    ).update(actor_identifier=models.F("created_by_id"))


class Migration(migrations.Migration):
    dependencies = [("prism", "0079_managed_credit_checkout_binding")]
    operations = [
        migrations.AddField(
            model_name="agentcccheckoutbinding",
            name="actor_identifier",
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(copy_existing_actor, migrations.RunPython.noop),
    ]
