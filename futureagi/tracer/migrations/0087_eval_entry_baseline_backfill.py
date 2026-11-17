"""One-time baseline backfill: stamp config_hash + correct status on legacy
live EvalLogger rows.

``atomic=False`` + the service's id-batched updates keep this off the single
giant-lock path on the multi-million-row table; the service is idempotent (it
only touches rows missing a hash / on the wrong status), so a retried deploy
resumes cleanly. The forward op imports the live hash service — unavoidable for
the transitive config hash, and acceptable for a one-time data backfill.
"""

from django.db import migrations

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:fi_cdc_outbox",
        "postgres:model_hub_composite_eval_child",
        "postgres:model_hub_evaltemplate",
        "postgres:tracer_custom_eval_config",
        "postgres:tracer_eval_logger",
    ],
    "mitigation": (
        "Stamps status and config_hash on live (deleted = false) tracer_eval_logger "
        "rows through tracer.services.eval_tasks.backfill, on the migration's own "
        "connection. Status pass: rows with error = true become errored and rows with "
        "a skipped_reason become skipped, in id-ordered keyset UPDATE ... RETURNING "
        "batches of 5000. Hash pass: one SELECT DISTINCT custom_eval_config_id over "
        "rows still missing a hash, then for each config it reads the live "
        "tracer_custom_eval_config row, its model_hub_evaltemplate and, for a "
        "composite template, its model_hub_composite_eval_child links and child "
        "templates, and stamps that config's null-hash rows in UPDATE batches of "
        "5000; rows whose config is gone or soft-deleted stay null. atomic = False: "
        "each UPDATE commits on its own. Row scale: every live eval result "
        "(multi-million rows); tracer_eval_logger is a registered high-volume table, "
        "the config, template and child-link tables are not. On a Standalone install "
        "in the default outbox CDC mode, the statement-level capture triggers on "
        "tracer_eval_logger also insert one fi_cdc_outbox row per updated row; "
        "fi_cdc_outbox is a registered high-volume table. The step itself runs no "
        "statement on fi_cdc_outbox and sends nothing to ClickHouse. Second run: a "
        "row already errored or skipped, or already holding a hash, is never "
        "rewritten. A re-run still changes data: it sets status on rows written "
        "since, and it stamps every row still missing a hash with the hash of its "
        "config as the config is at re-run time, so results produced before a later "
        "config change are marked as matching the changed config. No schema change; "
        "the reverse leaves the data in place."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def _forward(apps, schema_editor):
    from tracer.services.eval_tasks.backfill import backfill_config_hash_and_status

    backfill_config_hash_and_status(db_connection=schema_editor.connection)


def _reverse(apps, schema_editor):
    # A baseline stamp isn't meaningfully reversible (we can't recover which rows
    # were null before); leave the data in place on rollback.
    pass


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("tracer", "0086_eval_entry_attempts"),
    ]

    operations = [
        migrations.RunPython(_forward, _reverse),
    ]
