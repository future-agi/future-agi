from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope


UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "clickhouse:model_hub_score",
        "postgres:model_hub_score",
    ],
    "mitigation": (
        "Backfills model_hub_score.value_updated_at where it is null: created_at "
        "when value_history is empty, else updated_at. Reads ids in pk-ordered "
        "keyset pages of 1000 (value_updated_at IS NULL and pk above the last id "
        "read) and writes each page with one UPDATE that repeats the IS NULL "
        "filter, so a value set by a current writer or by an earlier run is never "
        "overwritten. atomic=False: each UPDATE commits on its own, so a re-run or "
        "a resumed run skips completed rows and is a no-op once no row is null; "
        "the reverse is a no-op. Row scale is every score row whose clock is null, "
        "which on an update is every score written before the column existed; "
        "each statement touches at most 1000 rows. No schema change; each UPDATE "
        "locks only the rows of its page. clickhouse:model_hub_score is affected "
        "through replication from postgres:model_hub_score; the step itself "
        "sends nothing to ClickHouse."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def preserve_existing_order(apps, schema_editor):
    with executor_alias_scope(schema_editor):
        # The schema migration is already recorded. Restarting this bounded backfill
        # skips completed rows and never overwrites values supplied by current writers.
        score = apps.get_model("model_hub", "Score")
        rows = score._base_manager.using((schema_editor.connection.alias if schema_editor is not None else DEFAULT_DB_ALIAS))
        cursor = None
        while True:
            page = rows.filter(value_updated_at__isnull=True)
            if cursor is not None:
                page = page.filter(pk__gt=cursor)
            ids = list(page.order_by("pk").values_list("pk", flat=True)[:1000])
            if not ids:
                break
            rows.filter(pk__in=ids, value_updated_at__isnull=True).update(
                # With no value history the original value is still present, so
                # created_at is exact even when notes were edited later. Changed
                # legacy values lack a separate clock; retain their recorded order.
                value_updated_at=models.Case(
                    models.When(value_history=[], then=models.F("created_at")),
                    default=models.F("updated_at"),
                    output_field=models.DateTimeField(),
                )
            )
            cursor = ids[-1]


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("model_hub", "0124_score_value_updated_at")]
    operations = [
        migrations.RunPython(preserve_existing_order, migrations.RunPython.noop),
    ]
