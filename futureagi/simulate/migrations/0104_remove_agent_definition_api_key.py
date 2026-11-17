from django.db import migrations
from tfc.migration_bridge import SkipDatabaseIfLegacyApplied
from tfc.migration_database import executor_alias_scope

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:simulate_agent_definition"],
    "mitigation": (
        "Before simulate_agent_definition.api_key is dropped, a check asks whether any "
        "row, soft-deleted rows included, still has an api_key that is not NULL and "
        "not empty. On PostgreSQL it first runs LOCK TABLE simulate_agent_definition "
        "IN SHARE ROW EXCLUSIVE MODE on the migration's own connection, so writes to "
        "simulate_agent_definition wait for the check and the drop. The lock is held "
        "only for that moment, inside the migration's one transaction, which ends "
        "right after the drop or the refusal. A wait longer than the connection's "
        "lock_timeout (default_direct sets one) fails the migration with nothing "
        "changed. Without one, the lock waits for open writes, the drop for open "
        "reads, and new reads queue behind the drop. If a transaction that read the "
        "table before the lock then writes, a deadlock error ends that transaction, "
        "or the migration with nothing changed. Other databases skip the lock. The "
        "check is "
        "one exists() query that returns no key value. If such a row exists the "
        "migration stops with: 'An agent definition still has an API key that is not "
        "stored encrypted. Stop every process of the old version, restore the database "
        "from the backup taken before this upgrade, and run the upgrade again.' The "
        "drop does not run and nothing is changed. The restore is needed because "
        "simulate.0103_encrypt_agent_definition_api_key is recorded and cannot be "
        "reversed while encrypted values exist, so a rerun without the restore refuses "
        "again. With every api_key NULL or empty the column is dropped as before. Safe "
        "to run again: the lock and the check write nothing, and a failed run rolls "
        "back whole. Every process of the old version must still be stopped first: it "
        "reads and writes api_key, which the drop removes. The check's reverse is a "
        "no-op; reversing re-creates the column empty. An install that recorded the "
        "earlier simulate.0102_remove_agent_definition_api_key skips both steps "
        "(SkipDatabaseIfLegacyApplied). Row scale: one row per agent definition; not a "
        "registered high-volume table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}

LEFTOVER_KEY_REFUSAL = (
    "An agent definition still has an API key that is not stored encrypted. Stop "
    "every process of the old version, restore the database from the backup taken "
    "before this upgrade, and run the upgrade again."
)


def require_empty_api_key_column(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        using = schema_editor.connection.alias
        agent = apps.get_model("simulate", "AgentDefinition")
        if schema_editor.connection.vendor == "postgresql":
            # Writes wait from here until the migration's transaction ends.
            schema_editor.execute(
                "LOCK TABLE %s IN SHARE ROW EXCLUSIVE MODE"
                % schema_editor.quote_name(agent._meta.db_table)
            )
        if (
            agent._base_manager.using(using)
            .exclude(api_key__isnull=True)
            .exclude(api_key="")
            .exists()
        ):
            raise ValueError(LEFTOVER_KEY_REFUSAL)


class Migration(migrations.Migration):
    dependencies = [
        ('simulate', '0103_encrypt_agent_definition_api_key'),
    ]
    # c2.5 recorded this as simulate.0102_remove_agent_definition_api_key.
    operations = [SkipDatabaseIfLegacyApplied(legacy=('simulate', '0102_remove_agent_definition_api_key'), operations=[
        migrations.RunPython(require_empty_api_key_column, migrations.RunPython.noop),
        migrations.RemoveField(model_name="agentdefinition", name="api_key"),
    ])]
