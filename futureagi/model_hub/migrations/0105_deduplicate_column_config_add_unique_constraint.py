"""Deduplicate ColumnConfig rows and add a unique constraint on
(table_name, organization, identifier) to prevent the race condition
that causes 'get() returned more than one ColumnConfig'.

For each group of duplicates, the most recently updated row is kept
and the rest are deleted.
"""

from django.db import migrations, models


def deduplicate_column_configs(apps, schema_editor):
    ColumnConfig = apps.get_model("model_hub", "ColumnConfig")
    from django.db.models import Count, Max

    dupes = (
        ColumnConfig.objects.values("table_name", "organization", "identifier")
        .annotate(cnt=Count("id"), latest=Max("updated_at"))
        .filter(cnt__gt=1)
    )

    total_deleted = 0
    for group in dupes:
        rows = ColumnConfig.objects.filter(
            table_name=group["table_name"],
            organization=group["organization"],
            identifier=group["identifier"],
        ).order_by("-updated_at")

        # Keep the first (most recently updated), delete the rest
        keep = rows.first()
        to_delete = rows.exclude(pk=keep.pk)
        deleted_count = to_delete.count()
        to_delete.delete()
        total_deleted += deleted_count

    if total_deleted:
        print(
            f"\n  Deleted {total_deleted} duplicate ColumnConfig rows."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0101_eval_usage_column_config_and_version_backfill"),
        ("model_hub", "0104_merge_20260526_0921"),
    ]

    operations = [
        migrations.RunPython(
            deduplicate_column_configs,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="columnconfig",
            constraint=models.UniqueConstraint(
                fields=["table_name", "organization", "identifier"],
                name="unique_column_config_per_table_org_identifier",
            ),
        ),
    ]
