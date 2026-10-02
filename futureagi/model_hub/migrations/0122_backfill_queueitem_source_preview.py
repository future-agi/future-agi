import os

from django.db import migrations

# Off by default. The backfill reads ClickHouse and fails open, so running it
# inside `migrate` costs full deploy time and — if CH is degraded — records this
# migration as applied having stamped nothing, with no retry. It is a cache
# warm-up: rows left NULL keep rendering via the live fallback, and the command
# is idempotent (guarded by source_preview__isnull=True), so re-running is free.
#
#   normal path:  python manage.py backfill_queue_item_source_preview
#   in-migration: ANNOTATION_PREVIEW_BACKFILL_ON_MIGRATE=1 python manage.py migrate
_ENV_FLAG = "ANNOTATION_PREVIEW_BACKFILL_ON_MIGRATE"


def _backfill(apps, schema_editor):
    if os.environ.get(_ENV_FLAG, "").lower() not in {"1", "true", "yes"}:
        return

    from model_hub.management.commands.backfill_queue_item_source_preview import (
        backfill_queue_item_source_previews,
    )

    backfill_queue_item_source_previews()


def _noop(apps, schema_editor):
    # Irreversible data backfill; nothing to undo (leaves values in place).
    pass


class Migration(migrations.Migration):
    # Chunked, per-project backfill that reads ClickHouse — not a single
    # transaction.
    atomic = False

    dependencies = [
        ("model_hub", "0121_queueitem_source_preview"),
    ]

    operations = [
        migrations.RunPython(_backfill, _noop),
    ]
