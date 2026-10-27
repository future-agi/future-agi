"""Backfill empty config_snapshot on composite eval V1 versions.

When a composite eval was first created, V1 was stored with
config_snapshot={}.  This migration populates those empty snapshots
from the live CompositeEvalChild rows and parent aggregation settings
so that version-switching in the UI works correctly.

Strictly additive — only touches versions where config_snapshot is
empty ({}).  Idempotent and safe to re-run.
"""

from django.db import migrations


def backfill_composite_v1_snapshots(apps, schema_editor):
    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")
    CompositeEvalChild = apps.get_model("model_hub", "CompositeEvalChild")

    # Find all composite templates
    composite_ids = list(
        EvalTemplate.objects.filter(
            template_type="composite",
            deleted=False,
        ).values_list("id", flat=True)
    )

    if not composite_ids:
        return

    # Find versions with empty config_snapshot
    empty_versions = EvalTemplateVersion.objects.filter(
        eval_template_id__in=composite_ids,
        config_snapshot={},
        deleted=False,
    )

    updated = 0
    for version in empty_versions:
        parent = EvalTemplate.objects.get(id=version.eval_template_id)
        links = (
            CompositeEvalChild.objects.filter(parent=parent, deleted=False)
            .select_related("child")
            .order_by("order")
        )
        if not links.exists():
            continue

        version.config_snapshot = {
            "aggregation_enabled": parent.aggregation_enabled,
            "aggregation_function": parent.aggregation_function,
            "composite_child_axis": parent.composite_child_axis or "",
            "children": [
                {
                    "child_id": str(link.child_id),
                    "child_name": link.child.name,
                    "order": link.order,
                    "weight": link.weight,
                    "config": link.config or {},
                    "pinned_version_id": (
                        str(link.pinned_version_id)
                        if link.pinned_version_id
                        else None
                    ),
                }
                for link in links
            ],
        }
        version.save(update_fields=["config_snapshot"])
        updated += 1

    if updated:
        print(f"\n  Backfilled {updated} composite eval version(s) with empty config_snapshot.")


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0100_merge_20260521_0749"),
    ]

    operations = [
        migrations.RunPython(
            backfill_composite_v1_snapshots,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
