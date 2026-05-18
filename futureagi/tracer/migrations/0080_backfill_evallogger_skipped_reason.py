"""TH-4910: backfill ``skipped_reason`` on rows the prior bug created.

Two shapes existed before this PR landed:

1. **Legacy prod shape** — ``error=True`` and ``error_message`` starting
   with "Error during evaluation: Required attribute". The dispatch
   raised on a missing span attribute and we wrote a generic error
   row. These rows render as "Fail" on customer dashboards — the bug
   Mudflap / Ghosted-Prod filed.

2. **Intermediate sentinel shape** — ``output_str =
   "__SKIPPED_MISSING_ATTRIBUTE__"`` with ``error=False``. Created by
   the first attempt at this fix (migration 0078). Only ever appears
   on local DBs that ran the earlier version.

Both shapes get rewritten to the ticket-prescribed shape: ``error=True``,
``skipped_reason = "missing_required_attribute: <attr>"``, and the
sentinel cleared from ``output_str``. The attribute name is parsed
from ``error_message`` (legacy) or ``output_metadata.missing_attribute``
(sentinel); when parsing fails we still set the prefix so the row is at
least classified as skipped.

Forward-only. Reverse is a no-op because the attribute name is also
re-derived from the same fields the forward pass overwrites.
"""

import re

from django.db import migrations


LEGACY_ERROR_PREFIX = "Error during evaluation: Required attribute"
STALE_SENTINEL = "__SKIPPED_MISSING_ATTRIBUTE__"
SKIPPED_REASON_PREFIX = "missing_required_attribute"
# Matches the format raised by ``EvalSkippedMissingAttribute.__init__``:
#   "Required attribute 'X' for key 'Y' not found for span Z"
_ATTR_RE = re.compile(r"Required attribute '([^']+)'")


def _reason_for(row):
    """Derive ``skipped_reason`` from whichever shape this row is in."""
    attr = None
    if row.error_message:
        match = _ATTR_RE.search(row.error_message)
        if match:
            attr = match.group(1)
    if not attr and isinstance(row.output_metadata, dict):
        attr = row.output_metadata.get("missing_attribute")
    if attr:
        return f"{SKIPPED_REASON_PREFIX}: {attr}"
    return SKIPPED_REASON_PREFIX


def forward(apps, schema_editor):
    EvalLogger = apps.get_model("tracer", "EvalLogger")

    # Legacy shape: error=True + "Required attribute" prefix.
    legacy = EvalLogger.objects.filter(
        error=True,
        error_message__startswith=LEGACY_ERROR_PREFIX,
        skipped_reason__isnull=True,
    )
    for row in legacy.iterator():
        row.skipped_reason = _reason_for(row)
        row.save(update_fields=["skipped_reason"])

    # Intermediate shape: sentinel in output_str. Flip error back to True
    # to match the new contract and clear the sentinel.
    sentinel = EvalLogger.objects.filter(output_str=STALE_SENTINEL)
    for row in sentinel.iterator():
        row.skipped_reason = _reason_for(row)
        row.error = True
        row.error_message = (
            row.error_message
            or f"Required attribute not found (backfilled from sentinel)"
        )
        row.output_str = None
        row.save(
            update_fields=[
                "skipped_reason",
                "error",
                "error_message",
                "output_str",
            ]
        )


def reverse(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("tracer", "0079_evallogger_skipped_reason"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
