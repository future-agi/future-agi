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

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:agentcc_api_key",
        "postgres:agentcc_ee_agent_key",
    ],
    "mitigation": (
        "Sets expires_at = created_at + 90 days on agentcc_api_key rows that have an "
        "Enterprise agent-key binding (a row in agentcc_ee_agent_key, joined by "
        "api_key_id) and no expiry. One UPDATE statement in the migration's "
        "transaction; keys with an expiry and keys without a binding are untouched. "
        "Replay-safe: only rows with expires_at IS NULL match, so a re-run changes "
        "nothing. Reverse is a no-op and keeps the expiries. Row scale: the Enterprise "
        "agent keys of all organizations, one per agentcc_ee_agent_key row."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


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
