# Five indexes on the tool-call table, built CONCURRENTLY so the table keeps
# taking writes (CREATE INDEX CONCURRENTLY can't run inside a transaction:
# atomic=False).
#
# A concurrent build that is interrupted (a deploy timeout, a lost connection, a
# cancelled job) leaves the index behind marked INVALID, and the migration is
# not recorded. The rerun must not stop on "already exists", so each index is
# built as the released 0029_request_log_unique_request_id does it: an INVALID
# index left by an earlier run is dropped first (DROP INDEX CONCURRENTLY IF
# EXISTS), then CREATE INDEX CONCURRENTLY IF NOT EXISTS keeps one a run already
# finished and builds the rest. SeparateDatabaseAndState: Django's state sees
# the same five AddIndex operations as before, written out as literals.

from django.db import migrations, models

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["index", "data_migration"],
    "tables": ["postgres:agentcc_tool_call"],
    "mitigation": (
        "Builds five indexes on agentcc_tool_call, each led by organization: "
        "started_at; agent_run_id; server_id, tool and started_at; api_key_id with "
        "started_at; generation_id. Each is built with CREATE INDEX CONCURRENTLY IF "
        "NOT EXISTS outside a transaction (atomic = False), from a RunPython in "
        "SeparateDatabaseAndState; Django's state sees the same five AddIndex "
        "operations. A build takes SHARE UPDATE EXCLUSIVE on the table: reads and "
        "writes go on, other schema changes and VACUUM wait, and the build waits for "
        "transactions older than it. An index an interrupted run left INVALID is "
        "dropped first with DROP INDEX CONCURRENTLY IF EXISTS, under the same lock; a "
        "valid one is kept. The RunPython writes no rows: it reads pg_index and runs "
        "only these index statements. Replay-safe: a rerun keeps the valid indexes "
        "and builds the rest, and the migration is recorded only after all five. "
        "Reverse drops the five concurrently. Row scale: one row per MCP tool call "
        "through the gateway, all organizations; build time grows with the table."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}

TABLE_MODEL = "agentcctoolcall"


_INVALID_SQL = """
SELECT 1
FROM pg_index i
JOIN pg_class c ON c.oid = i.indexrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relname = %s AND n.nspname = current_schema() AND NOT i.indisvalid
"""
_CREATE = "CREATE INDEX CONCURRENTLY "


def _resumable_create_sql(index, model, schema_editor):
    sql = str(index.create_sql(model, schema_editor, concurrently=True))
    if not sql.startswith(_CREATE):
        raise RuntimeError(f"unexpected index statement for {index.name}")
    return _CREATE + "IF NOT EXISTS " + sql[len(_CREATE) :]


def _indexes():
    """The five indexes, as the state operations below declare them."""
    return [op.index for op in Migration.operations[0].state_operations]


def _forwards(apps, schema_editor):
    model = apps.get_model("prism", TABLE_MODEL)
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        for index in _indexes():
            cursor.execute(_INVALID_SQL, [index.name])
            if cursor.fetchone() is not None:
                cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quote(index.name)}")
            cursor.execute(_resumable_create_sql(index, model, schema_editor))


def _backwards(apps, schema_editor):
    quote = schema_editor.quote_name
    with schema_editor.connection.cursor() as cursor:
        for index in reversed(_indexes()):
            cursor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {quote(index.name)}")


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("prism", "0040_mcp_oauth"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddIndex(
                    model_name=TABLE_MODEL,
                    index=models.Index(
                        fields=["organization", "-started_at"],
                        name="agentcc_tc_org_started_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name=TABLE_MODEL,
                    index=models.Index(
                        fields=["organization", "agent_run_id"],
                        name="agentcc_tc_org_run_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name=TABLE_MODEL,
                    index=models.Index(
                        fields=["organization", "server_id", "tool", "-started_at"],
                        name="agentcc_tc_org_server_tool_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name=TABLE_MODEL,
                    index=models.Index(
                        fields=["organization", "api_key_id", "-started_at"],
                        name="agentcc_tc_org_key_idx",
                    ),
                ),
                migrations.AddIndex(
                    model_name=TABLE_MODEL,
                    index=models.Index(
                        fields=["organization", "generation_id"],
                        name="agentcc_tc_org_generation_idx",
                    ),
                ),
            ],
            database_operations=[migrations.RunPython(_forwards, _backwards)],
        ),
    ]
