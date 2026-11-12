"""Permanently retire credentials identified by Enterprise provisioning records."""

from django.db import DEFAULT_DB_ALIAS, migrations
from django.utils import timezone

from tfc.migration_database import executor_alias_scope


def retire_credentials(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        alias = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        credentials = apps.get_model("prism", "AgentccEEServiceCredential")
        bindings = apps.get_model("prism", "AgentccEEAgentKey")
        keys = apps.get_model("prism", "AgentccAPIKey")
        now = timezone.now()
        credentials.objects.using(alias).exclude(status="revoked").update(
            status="revoked", revoked_at=now
        )
        # The authoritative binding includes older rows without an issuer or user.
        bound_ids = bindings.objects.using(alias).values_list("api_key_id", flat=True)
        keys._base_manager.using(alias).filter(pk__in=bound_ids).exclude(
            status="revoked"
        ).update(status="revoked", updated_at=now)


class Migration(migrations.Migration):
    dependencies = [("prism", "0057_mcp_credential_origin")]
    # Credentials stay retired across an image rollback. Recovery is not reissuance.
    operations = [migrations.RunPython(retire_credentials, migrations.RunPython.noop)]
