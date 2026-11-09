"""CN22c fix round 1 (review R-CN22c-6): agent keys CN22 minted before CN22c
have no expiry. Each gets the 90-day lifetime every agent key now has,
counted from its mint (created_at + 90 days), so none stays live and under
the org's cap forever. Keys with an expiry, and keys that are not Omega EE's
agent keys, are left as they are. The gateway learns the expiry at its next
key sync. Reversing leaves the expiries in place."""

from datetime import timedelta

from django.db import DEFAULT_DB_ALIAS, migrations
from django.db.models import F

from tfc.migration_database import executor_alias_scope

# agentcc.services.ee_agent_key_policy.LIFETIME, frozen here: a migration
# does not follow later changes to the constant.
LIFETIME = timedelta(days=90)


def backfill_agent_key_expiry(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        db = (schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS)
        AgentccAPIKey = apps.get_model("prism", "AgentccAPIKey")
        AgentccAPIKey._base_manager.using(db).filter(
            ee_agent_key__isnull=False, expires_at__isnull=True
        ).update(expires_at=F("created_at") + LIFETIME)


class Migration(migrations.Migration):

    dependencies = [
        ("prism", "0053_ee_agent_key_policy"),
    ]

    operations = [
        migrations.RunPython(backfill_agent_key_expiry, migrations.RunPython.noop),
    ]
