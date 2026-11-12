from django.db import DEFAULT_DB_ALIAS, migrations, models

from tfc.migration_database import executor_alias_scope


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
