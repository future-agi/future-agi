"""Per-tenant runtime wiring on EvalGroundTruth.

Adds typed columns for the GT setup knobs that used to live in
EvalTemplate.config["ground_truth"], backfills them from existing rows,
then strips the key from every template config. After this, the GT
runtime config is read from the tenant-scoped EvalGroundTruth row -
SYSTEM templates no longer carry a shared pointer that other tenants
would inherit.
"""

from django.db import migrations, models
from django.db.models import Q


def backfill_then_strip_template_config(apps, _schema_editor):
    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalGroundTruth = apps.get_model("model_hub", "EvalGroundTruth")

    batch_size = 200
    batch: list = []

    qs = (
        EvalTemplate.objects
        .filter(config__has_key="ground_truth")
        .only("id", "config")
        .iterator(chunk_size=batch_size)
    )
    for tmpl in qs:
        cfg = tmpl.config or {}
        gt_cfg = cfg.get("ground_truth")
        if not isinstance(gt_cfg, dict):
            continue
        gt_id = gt_cfg.get("ground_truth_id")
        if gt_id:
            EvalGroundTruth.objects.filter(id=gt_id, deleted=False).update(
                is_active=True,
                enabled=bool(gt_cfg.get("enabled", True)),
                max_examples=int(gt_cfg.get("max_examples") or 3),
                similarity_threshold=float(gt_cfg.get("similarity_threshold") or 0.7),
            )
        cfg.pop("ground_truth", None)
        tmpl.config = cfg
        batch.append(tmpl)
        if len(batch) >= batch_size:
            EvalTemplate.objects.bulk_update(batch, fields=["config"])
            batch.clear()
    if batch:
        EvalTemplate.objects.bulk_update(batch, fields=["config"])


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0111_userevalmetric_pinned_version"),
    ]

    operations = [
        migrations.AddField(
            model_name="evalgroundtruth",
            name="is_active",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="evalgroundtruth",
            name="enabled",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="evalgroundtruth",
            name="max_examples",
            field=models.PositiveSmallIntegerField(default=3),
        ),
        migrations.AddField(
            model_name="evalgroundtruth",
            name="similarity_threshold",
            field=models.FloatField(default=0.7),
        ),
        migrations.RunPython(
            backfill_then_strip_template_config,
            reverse_code=migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="evalgroundtruth",
            constraint=models.UniqueConstraint(
                fields=["eval_template", "organization", "workspace"],
                condition=Q(deleted=False, is_active=True),
                name="uniq_active_gt_per_tenant_template",
                nulls_distinct=False,
            ),
        ),
    ]
