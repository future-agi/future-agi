# One row per gateway request. The gateway resends a batch of request logs when
# the backend answers too late, and every resend added the same rows again.
#
#   - atomic=False so CREATE INDEX CONCURRENTLY runs outside a transaction and
#     takes no ACCESS EXCLUSIVE lock on the large request log table.
#   - The duplicates already saved are soft-deleted first, keeping the oldest
#     row per (organization, request_id), so the unique build cannot fail on them.
#   - Pods still on the old code keep saving resent batches during the deploy,
#     so a duplicate can land between the dedupe and the build and fail it. A
#     failed build leaves an INVALID index, so it is dropped, the dedupe runs
#     again, and the build is retried.
#   - SeparateDatabaseAndState: Django state sees the AddConstraint; the
#     database builds the same partial unique index concurrently.

from django.db import IntegrityError, migrations, models

_INDEX = "agentcc_req_log_live_request_uniq"

_DEDUP_SQL = """
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY organization_id, request_id
               ORDER BY created_at ASC, id ASC
           ) AS rn
    FROM agentcc_request_log
    WHERE deleted = false
      AND request_id <> ''
)
UPDATE agentcc_request_log AS t
SET deleted = true,
    deleted_at = NOW()
FROM ranked
WHERE t.id = ranked.id
  AND ranked.rn > 1;
"""

_DROP_INDEX_SQL = f"DROP INDEX CONCURRENTLY IF EXISTS {_INDEX};"

_CREATE_INDEX_SQL = f"""
CREATE UNIQUE INDEX CONCURRENTLY {_INDEX}
ON agentcc_request_log (organization_id, request_id)
WHERE NOT deleted AND request_id <> '';
"""


_BUILD_ATTEMPTS = 3


def _dedupe_and_build_index(connection):
    with connection.cursor() as cursor:
        for attempt in range(1, _BUILD_ATTEMPTS + 1):
            cursor.execute(_DEDUP_SQL)
            cursor.execute(_DROP_INDEX_SQL)
            try:
                cursor.execute(_CREATE_INDEX_SQL)
                return
            except IntegrityError:
                if attempt == _BUILD_ATTEMPTS:
                    cursor.execute(_DROP_INDEX_SQL)
                    raise


def _forwards(apps, schema_editor):
    _dedupe_and_build_index(schema_editor.connection)


def _backwards(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(_DROP_INDEX_SQL)


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("prism", "0028_drop_rejected_org_config_keys"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AddConstraint(
                    model_name="agentccrequestlog",
                    constraint=models.UniqueConstraint(
                        condition=models.Q(("deleted", False))
                        & ~models.Q(("request_id", "")),
                        fields=("organization", "request_id"),
                        name=_INDEX,
                    ),
                ),
            ],
            database_operations=[migrations.RunPython(_forwards, _backwards)],
        ),
    ]
