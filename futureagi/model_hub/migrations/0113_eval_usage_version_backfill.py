"""Backfill version info on existing APICallLog entries.

For every USER-created eval template:
  1. If the template has no EvalTemplateVersion at all → create v1 first
     (snapshotting its current config/criteria/model), marked is_default.
  2. Stamp the template's untagged usage logs with the (now-guaranteed)
     default version_id + version_number in config JSONField.

System templates are intentionally left alone — their Usage tab shows
a dash, which matches product expectation. Going forward, runtime
version-stamping populates new entries.
"""

import logging

from django.db import migrations

logger = logging.getLogger(__name__)


def backfill_apicalllog_version_info(apps, schema_editor):
    try:
        APICallLog = apps.get_model("usage", "APICallLog")
    except LookupError:
        # OSS build — usage app not installed
        return

    # First: unwrap double-encoded JSON strings. Some entries have config
    # stored as a JSON string containing a JSON object (json.dumps was
    # called before saving to a JSONField). This makes JSONB operators
    # like ->> unusable. Unwrap them to proper JSONB objects.
    from django.db import connection

    with connection.cursor() as cursor:
        # Only unwrap rows whose inner text is a JSON object or array.
        # A plain scalar string (e.g. "foo") would make (config #>> '{}')::jsonb
        # raise a syntax error and abort the migration, so we guard with
        # LEFT(..., 1) IN ('{', '[').
        cursor.execute(
            "UPDATE usage_apicalllog "
            "SET config = (config #>> '{}')::jsonb "
            "WHERE deleted = false "
            "  AND jsonb_typeof(config) = 'string' "
            "  AND LEFT(config #>> '{}', 1) IN ('{', '[')"
        )
        unwrapped = cursor.rowcount
        if unwrapped:
            logger.info(f"\n  Unwrapped {unwrapped} double-encoded APICallLog configs.")

    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    # Filter to user-owned templates only. System templates intentionally
    # stay unversioned — their Usage tab shows a dash.
    templates = EvalTemplate.objects.filter(deleted=False, owner="user")

    versions_created = 0
    total_updated = 0
    for template in templates.iterator():
        default_version = (
            EvalTemplateVersion.objects.filter(
                eval_template_id=template.id,
                is_default=True,
                deleted=False,
            )
            .order_by("-version_number")
            .first()
        )
        if not default_version:
            # Fall back to any non-deleted version with lowest number.
            default_version = (
                EvalTemplateVersion.objects.filter(
                    eval_template_id=template.id,
                    deleted=False,
                )
                .order_by("version_number")
                .first()
            )

        if not default_version:
            # No version exists at all — create v1 as the default. Snapshot
            # the template's current config/criteria/model so the version
            # actually describes what the template looks like today.
            try:
                default_version = EvalTemplateVersion.objects.create(
                    eval_template_id=template.id,
                    version_number=1,
                    is_default=True,
                    prompt_messages=[],
                    config_snapshot=template.config or {},
                    criteria=template.criteria or "",
                    model=template.model or "",
                    organization_id=template.organization_id,
                    workspace_id=template.workspace_id,
                    output_type_normalized=getattr(
                        template, "output_type_normalized", None
                    ),
                    pass_threshold=getattr(template, "pass_threshold", None),
                    choice_scores=getattr(template, "choice_scores", None),
                    error_localizer_enabled=getattr(
                        template, "error_localizer_enabled", False
                    ),
                    eval_tags=list(getattr(template, "eval_tags", []) or []),
                )
                versions_created += 1
            except Exception as e:
                logger.warning(
                    "Failed to create v1 for template",
                    template_id=str(template.id),
                    error=str(e),
                )
                continue

        version_id = str(default_version.id)
        version_number = default_version.version_number

        # Stamp untagged logs in 500-row batches.
        logs = APICallLog.objects.filter(
            source_id=str(template.id),
            deleted=False,
        )
        batch = []
        for log in logs.iterator(chunk_size=500):
            config = log.config
            if not isinstance(config, dict):
                continue
            if config.get("version_id"):
                continue
            config["version_id"] = version_id
            config["version_number"] = version_number
            log.config = config
            batch.append(log)
            if len(batch) >= 500:
                APICallLog.objects.bulk_update(batch, ["config"])
                total_updated += len(batch)
                batch = []
        if batch:
            APICallLog.objects.bulk_update(batch, ["config"])
            total_updated += len(batch)

    if versions_created:
        logger.info(f"Created v1 for {versions_created} user templates.")
    if total_updated:
        logger.info(
            f"Backfilled version info on {total_updated} APICallLog entries."
        )


class Migration(migrations.Migration):
    # Run outside a transaction so each bulk_update batch commits independently.
    # Without this the entire backfill (potentially millions of rows) holds a
    # write lock on usage_apicalllog for its full duration, which causes
    # migration timeouts on large tables and blocks all concurrent writes.
    atomic = False

    dependencies = [
        # Renumbered from 0112 → 0113 to resolve the leaf conflict with
        # dev's 0112_eval_ground_truth_tenant_scope. Chain after that one.
        ("model_hub", "0112_eval_ground_truth_tenant_scope"),
    ]

    operations = [
        migrations.RunPython(
            backfill_apicalllog_version_info,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
