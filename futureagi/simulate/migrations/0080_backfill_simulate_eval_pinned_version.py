"""Backfill SimulateEvalConfig.pinned_version for existing custom-eval bindings.

Bindings created before pinning existed resolve to whatever the template
default happens to be at run time, so editing a custom eval retroactively
changes what past simulations ran. Pin each one to the version that would
run today, which freezes current behaviour rather than altering it.

User-owned templates only. System evals carry no version and must stay
NULL. Idempotent: the pinned_version__isnull filter makes a re-run a no-op.
Reverse is a no-op.
"""

from django.db import migrations

BATCH_SIZE = 500


def _default_version(version_model, template_id):
    """Mirror EvalTemplateVersion.objects.get_default without the manager.

    apps.get_model() returns a plain Manager, so get_default() and
    create_version() are unavailable inside migrations.
    """
    version = (
        version_model.objects.filter(
            eval_template_id=template_id, is_default=True, deleted=False
        )
        .order_by("-version_number")
        .first()
    )
    if version is not None:
        return version
    return (
        version_model.objects.filter(eval_template_id=template_id, deleted=False)
        .order_by("-version_number")
        .first()
    )


def backfill(apps, schema_editor):
    SimulateEvalConfig = apps.get_model("simulate", "SimulateEvalConfig")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    pending = (
        SimulateEvalConfig.objects.filter(
            pinned_version__isnull=True,
            deleted=False,
            eval_template__owner="user",
        )
        .select_related("eval_template")
        .order_by("id")
    )

    resolved_by_template = {}
    batch = []
    pinned = 0
    no_versions = 0

    for config in pending.iterator(chunk_size=BATCH_SIZE):
        template_id = config.eval_template_id
        if template_id not in resolved_by_template:
            resolved_by_template[template_id] = _default_version(
                EvalTemplateVersion, template_id
            )
        version = resolved_by_template[template_id]
        if version is None:
            # Template has no versions at all, so leave NULL and let runtime keep
            # falling back to the live template.
            no_versions += 1
            continue

        config.pinned_version = version
        batch.append(config)
        pinned += 1

        if len(batch) >= BATCH_SIZE:
            SimulateEvalConfig.objects.bulk_update(batch, ["pinned_version"])
            batch = []

    if batch:
        SimulateEvalConfig.objects.bulk_update(batch, ["pinned_version"])

    print(
        f"[0080] SimulateEvalConfig pin backfill: "
        f"pinned={pinned} skipped_no_versions={no_versions}"
    )


def reverse(apps, schema_editor):
    # No-op. See module docstring.
    return


class Migration(migrations.Migration):

    dependencies = [
        ("simulate", "0079_simulateevalconfig_pinned_version"),
        ("model_hub", "0073_add_eval_template_version"),
    ]

    operations = [
        migrations.RunPython(backfill, reverse, elidable=False),
    ]
