from django.db import DEFAULT_DB_ALIAS, migrations, models
from django.utils import timezone

from accounts.api_key_constants import RETIRED_KEY_TYPES, RETIREMENT_REASON

from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:accounts_orgapikey",
    ],
    "mitigation": (
        "Atomic (Django default; Postgres DDL is transactional), one transaction on "
        "accounts_orgapikey: three ADD COLUMN statements for nullable columns "
        "(retired_at, retired_reason, prior_enabled; metadata only), then two "
        "UPDATEs. The first, for every key of a retired type (system, mcp, ingest) "
        "not yet retired, copies enabled into prior_enabled, sets enabled false, "
        "retired_at now and retired_reason '733'; the second keeps every key retired "
        "for '733' disabled. It never touches a secret, hash or ciphertext column. "
        "The two AlterFields change only the model's default and choices for name "
        "and type (max_length 100 and 50 as before), so no DDL. Lock: the first "
        "ALTER TABLE takes ACCESS EXCLUSIVE on accounts_orgapikey and holds it until "
        "the UPDATEs commit, so key lookups wait for the whole run. If it fails "
        "part-way, the transaction rolls back: no column added, no key disabled, and "
        "the migration is not recorded; re-run it as is. A re-run after success "
        "changes nothing (the first UPDATE skips retired rows). If the lock wait is "
        "the failure, re-run when traffic is low. Reverse (migrate accounts to "
        "0030): re-enables each key it retired from prior_enabled and clears the "
        "three fields; the columns then drop with the reverse of AddField."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}


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
