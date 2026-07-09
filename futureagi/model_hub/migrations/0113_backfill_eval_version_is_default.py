"""Backfill is_default=True on the highest-numbered version for orphan templates + enforce the unique-default invariant at the DB layer."""

import logging

from django.db import migrations, models


def backfill_is_default(apps, schema_editor):
    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    templates_needing_fix = (
        EvalTemplate.objects.filter(deleted=False, versions__deleted=False)
        .annotate(
            default_count=models.Count(
                "versions", filter=models.Q(versions__is_default=True, versions__deleted=False)
            ),
        )
        .filter(default_count=0)
        .distinct()
    )

    fixed = 0
    for template in templates_needing_fix.iterator():
        latest = (
            EvalTemplateVersion.objects.filter(
                eval_template=template, deleted=False
            )
            .order_by("-version_number")
            .first()
        )
        if latest is None:
            continue
        latest.is_default = True
        latest.save(update_fields=["is_default"])
        fixed += 1

    if fixed:
        logging.getLogger(__name__).info(
            "Backfilled is_default=True on latest version for %d templates.", fixed
        )


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0112_eval_ground_truth_tenant_scope"),
    ]

    operations = [
        # Repair orphan templates FIRST, then lock the invariant. Order matters
        # only conceptually — the constraint accepts zero-default rows, so it
        # would apply either way, but keeping the sequence clean.
        migrations.RunPython(backfill_is_default, reverse_code=migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="evaltemplateversion",
            constraint=models.UniqueConstraint(
                fields=["eval_template"],
                condition=models.Q(is_default=True, deleted=False),
                name="unique_default_version_per_template",
            ),
        ),
    ]
