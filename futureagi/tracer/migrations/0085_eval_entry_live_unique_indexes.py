# Hand-edited for production safety (TH-5978 PR 3b):
#   - atomic=False so CREATE/DROP INDEX CONCURRENTLY run outside a transaction.
#   - One §3.2 partial unique index per target_type, keyed on that row's
#     identity column (span->observation_span, trace->trace, session->
#     trace_session), scoped to live work-items (NOT deleted, non-null task).
#   - Each index is created via SeparateDatabaseAndState: Django state sees the
#     AddConstraint; the DB builds it CONCURRENTLY to avoid an ACCESS EXCLUSIVE
#     lock on the large prod table.
#   - A one-time dedup per index key runs FIRST (keep a non-errored row over an
#     errored one, then newest) so the unique build can't fail on the raced
#     legacy duplicates the old check-then-insert path left behind.

from django.db import migrations, models

_KEYS = [
    ("eval_logger_live_span_uniq", "observation_span_id", "span"),
    ("eval_logger_live_trace_uniq", "trace_id", "trace"),
    ("eval_logger_live_session_uniq", "trace_session_id", "session"),
]


def _dedup_sql(target_type, id_col):
    # Keep the best live row per (task, <identity>, eval): non-errored first,
    # then newest. Soft-delete the rest. Idempotent.
    return f"""
WITH ranked AS (
    SELECT id,
           ROW_NUMBER() OVER (
               PARTITION BY eval_task_id, {id_col}, custom_eval_config_id
               ORDER BY error ASC, created_at DESC, id DESC
           ) AS rn
    FROM tracer_eval_logger
    WHERE deleted = false
      AND eval_task_id IS NOT NULL
      AND target_type = '{target_type}'
)
UPDATE tracer_eval_logger AS t
SET deleted = true,
    deleted_at = NOW()
FROM ranked
WHERE t.id = ranked.id
  AND ranked.rn > 1;
"""


def _create_index_sql(name, id_col, target_type):
    return f"""
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS {name}
ON tracer_eval_logger (eval_task_id, {id_col}, custom_eval_config_id)
WHERE target_type = '{target_type}' AND NOT deleted AND eval_task_id IS NOT NULL;
"""


def _drop_index_sql(name):
    return f"DROP INDEX CONCURRENTLY IF EXISTS {name};"


_STATE_CONSTRAINTS = {
    "span": models.UniqueConstraint(
        condition=models.Q(
            ("deleted", False), ("eval_task_id__isnull", False), ("target_type", "span")
        ),
        fields=("eval_task_id", "observation_span", "custom_eval_config"),
        name="eval_logger_live_span_uniq",
    ),
    "trace": models.UniqueConstraint(
        condition=models.Q(
            ("deleted", False), ("eval_task_id__isnull", False), ("target_type", "trace")
        ),
        fields=("eval_task_id", "trace", "custom_eval_config"),
        name="eval_logger_live_trace_uniq",
    ),
    "session": models.UniqueConstraint(
        condition=models.Q(
            ("deleted", False),
            ("eval_task_id__isnull", False),
            ("target_type", "session"),
        ),
        fields=("eval_task_id", "trace_session", "custom_eval_config"),
        name="eval_logger_live_session_uniq",
    ),
}


class Migration(migrations.Migration):

    atomic = False

    dependencies = [
        ("tracer", "0084_eval_entry_work_item_columns"),
    ]

    operations = (
        # Dedup every target_type first so the concurrent builds can't fail.
        [
            migrations.RunSQL(
                sql=_dedup_sql(target_type, id_col),
                reverse_sql=migrations.RunSQL.noop,
            )
            for (_name, id_col, target_type) in _KEYS
        ]
        # Then build each partial unique index concurrently.
        + [
            migrations.SeparateDatabaseAndState(
                state_operations=[
                    migrations.AddConstraint(
                        model_name="evallogger",
                        constraint=_STATE_CONSTRAINTS[target_type],
                    )
                ],
                database_operations=[
                    migrations.RunSQL(
                        sql=_create_index_sql(name, id_col, target_type),
                        reverse_sql=_drop_index_sql(name),
                    )
                ],
            )
            for (name, id_col, target_type) in _KEYS
        ]
    )
