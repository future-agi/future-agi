from django.db import DEFAULT_DB_ALIAS, migrations, models
from django.utils import timezone

from accounts.api_key_constants import RETIRED_KEY_TYPES, RETIREMENT_REASON

from tfc.migration_database import executor_alias_scope


def retire(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Key = apps.get_model("accounts", "OrgApiKey")
        rows = Key._base_manager.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
        # Preserve the original state once, including already-disabled rows. Do
        # not overwrite another retirement or touch secret/hash/ciphertext fields.
        rows.filter(
            type__in=RETIRED_KEY_TYPES,
            retired_at__isnull=True,
            retired_reason__isnull=True,
        ).update(
            prior_enabled=models.F("enabled"),
            enabled=False,
            retired_at=timezone.now(),
            retired_reason=RETIREMENT_REASON,
        )
        rows.filter(type__in=RETIRED_KEY_TYPES, retired_reason=RETIREMENT_REASON).update(
            enabled=False
        )


def restore(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        Key = apps.get_model("accounts", "OrgApiKey")
        Key._base_manager.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)).filter(
            retired_reason=RETIREMENT_REASON, prior_enabled__isnull=False
        ).update(
            enabled=models.F("prior_enabled"),
            retired_at=None,
            retired_reason=None,
            prior_enabled=None,
        )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0030_merge_omega_purge_and_simulation_agent_key")]
    operations = [
        migrations.AddField(
            model_name="orgapikey",
            name="retired_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="orgapikey",
            name="retired_reason",
            field=models.CharField(max_length=64, null=True, blank=True),
        ),
        migrations.AddField(
            model_name="orgapikey",
            name="prior_enabled",
            field=models.BooleanField(null=True, blank=True),
        ),
        migrations.AlterField(
            model_name="orgapikey",
            name="name",
            field=models.CharField(max_length=100, default="Personal API key"),
        ),
        migrations.AlterField(
            model_name="orgapikey",
            name="type",
            field=models.CharField(
                max_length=50, default="user", choices=[("user", "User")]
            ),
        ),
        migrations.RunPython(retire, restore),
    ]
