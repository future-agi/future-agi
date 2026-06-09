"""Backfill version info on existing APICallLog entries.

Tags each eval template's usage logs with the template's current
default version (version_id + version_number in the config JSONField).
This is imperfect for historical entries — they get the current default,
not necessarily the version that actually ran — but it's the best we
can do since version info was never tracked before. Going forward, all
new entries will have the correct version.
"""

from django.db import migrations


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
        cursor.execute(
            "UPDATE usage_apicalllog "
            "SET config = (config #>> '{}')::jsonb "
            "WHERE deleted = false AND jsonb_typeof(config) = 'string'"
        )
        unwrapped = cursor.rowcount
        if unwrapped:
            print(f"\n  Unwrapped {unwrapped} double-encoded APICallLog configs.")

    EvalTemplate = apps.get_model("model_hub", "EvalTemplate")
    EvalTemplateVersion = apps.get_model("model_hub", "EvalTemplateVersion")

    templates = EvalTemplate.objects.filter(deleted=False).values_list(
        "id", flat=True
    )

    total_updated = 0
    for template_id in templates:
        default_version = (
            EvalTemplateVersion.objects.filter(
                eval_template_id=template_id,
                is_default=True,
                deleted=False,
            )
            .first()
        )
        if not default_version:
            # Fall back to the first version
            default_version = (
                EvalTemplateVersion.objects.filter(
                    eval_template_id=template_id,
                    deleted=False,
                )
                .order_by("version_number")
                .first()
            )
        if not default_version:
            continue

        version_id = str(default_version.id)
        version_number = default_version.version_number

        # Update logs that don't have version info yet
        logs = APICallLog.objects.filter(
            source_id=str(template_id),
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

    if total_updated:
        print(
            f"\n  Backfilled version info on {total_updated} APICallLog entries."
        )


class Migration(migrations.Migration):

    dependencies = [
        ("model_hub", "0110_merge_20260609_1253"),
    ]

    operations = [
        migrations.RunPython(
            backfill_apicalllog_version_info,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
