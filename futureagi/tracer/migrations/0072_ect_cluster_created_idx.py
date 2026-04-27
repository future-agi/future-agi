from django.db import migrations, models


class Migration(migrations.Migration):
    """Add (cluster, -created_at) index on ErrorClusterTraces.

    Hot paths:
      - ``_fetch_latest_trace_id_batch`` (DISTINCT ON cluster, ORDER BY -created_at)
        — list page + detail header.
      - Trends queries that filter by cluster + created_at range.

    Done CONCURRENTLY so the table isn't locked while the index builds.
    ``SeparateDatabaseAndState`` lets Django track the index in model
    state without re-running CREATE INDEX on already-indexed tables.
    """

    atomic = False

    dependencies = [
        ("tracer", "0071_observation_span_eval_attributes_gin"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "CREATE INDEX CONCURRENTLY IF NOT EXISTS "
                        "tracer_ect_cluster_created_idx "
                        "ON public.tracer_error_cluster_traces "
                        "(cluster_id, created_at DESC);"
                    ),
                    reverse_sql=(
                        "DROP INDEX CONCURRENTLY IF EXISTS "
                        "tracer_ect_cluster_created_idx;"
                    ),
                ),
            ],
            state_operations=[
                migrations.AddIndex(
                    model_name="errorclustertraces",
                    index=models.Index(
                        fields=["cluster", "-created_at"],
                        name="tracer_ect_cluster_created_idx",
                    ),
                ),
            ],
        ),
    ]
