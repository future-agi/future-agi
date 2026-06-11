"""Drop two unused GIN indexes on tracer_observation_span.

Background:
  - ``tracer_obse_span_at_gin`` (77 GB, GenAI schema, added in 0047) and
    ``tracer_obse_eval_attr_gin`` (33 GB, simulate lookup, added in 0071)
    were both serving zero queries (``idx_scan = 0`` since stats inception)
    while accounting for ~96% of write-side disk reads on the table.
  - That write amplification was pushing ``COPY tracer_observation_span``
    past the 30s ``statement_timeout``, causing the
    ``bulk_create_observation_span_task`` ingestion activity to fail
    in a steady stream.
  - The indexes were dropped manually in production on 2026-05-01 via
    ``DROP INDEX CONCURRENTLY`` (110 GB freed, failure rate -> 0).
    This migration codifies that change so a fresh deploy doesn't
    recreate them and so model state matches DB state.

Read paths that previously could have used these indexes have been
moved to ClickHouse (which has the data in shredded ``span_attr_str``
maps and ``span_attributes_raw`` JSON, with much faster lookups).
"""

from django.db import migrations


class Migration(migrations.Migration):
    # CONCURRENTLY cannot run in a transaction.
    atomic = False

    dependencies = [
        ("tracer", "0073_merge_20260428_1309"),
    ]

    operations = [
        # 1) GenAI schema GIN on span_attributes (77 GB, never used).
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "public.tracer_obse_span_at_gin;"
                    ),
                    reverse_sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "tracer_obse_span_at_gin "
                        "ON public.tracer_observation_span "
                        "USING gin (span_attributes);"
                    ),
                ),
            ],
            state_operations=[
                migrations.RemoveIndex(
                    model_name="observationspan",
                    name="tracer_obse_span_at_gin",
                ),
            ],
        ),
        # 2) Simulate lookup GIN on eval_attributes (33 GB, never used).
        # Was created via raw SQL in 0071 and never declared in the model,
        # so no state_operations are needed.
        migrations.RunSQL(
            sql=(
                "DROP INDEX CONCURRENTLY IF EXISTS "
                "public.tracer_obse_eval_attr_gin;"
            ),
            reverse_sql=(
                "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                "tracer_obse_eval_attr_gin "
                "ON public.tracer_observation_span "
                "USING gin (eval_attributes jsonb_path_ops);"
            ),
        ),
    ]
