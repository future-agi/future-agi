"""Repair historical eval templates where no version carries is_default=True.

The original ``EvalTemplateVersionManager.create_version`` only marked the
FIRST version as default (``is_default=is_first``) and never rotated the
flag on subsequent creates. If v1 ever got soft-deleted, the template was
left with N versions all ``is_default=False`` — an ambiguous state that
made backend and FE disagree on which version was "current" (backend
falls back to highest version_number, FE was falling back to lowest).

Starting from this migration, ``create_version`` always sets the new
version as default and demotes the previous one. This one-shot pass
fixes existing templates: for every EvalTemplate with no version flagged
as default, mark the version with the highest ``version_number`` as
default. Templates that already have a version flagged are left alone.
"""

from django.db import migrations, models


def backfill_is_default(apps, schema_editor):
    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    # Templates that have versions but none flagged as default.
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
        import logging
        logging.getLogger(__name__).info(
            "Backfilled is_default=True on latest version for %d templates.", fixed
        )


class Migration(migrations.Migration):
    dependencies = [
        ("model_hub", "0113_eval_usage_version_backfill"),
    ]

    operations = [
        migrations.RunPython(backfill_is_default, reverse_code=migrations.RunPython.noop),
    ]
