"""Saved service-provider error-rate monitors keep alerting exactly as before.

``service_provider_error_rates`` now measures the share of providers that had
at least one ERROR span in the window. The production build measured the share
that had none. Over the same providers the new value is 1 - the old value,
exactly.

For each static monitor on that metric whose numbers are still in the old
meaning (``threshold_rule`` "earlier", from 0133), this flips the operator,
replaces each threshold ``v`` with ``1 - v`` and marks the row "current":
``greater_than v`` becomes ``less_than 1 - v`` and ``less_than v`` becomes
``greater_than 1 - v``, critical and warning alike, so the row fires on exactly
the windows it fired on before. The critical/warning order the serializer
checks still holds after the flip. ``1 - v`` is computed on the decimal digits
the number was saved with (0.95 -> 0.05, never 0.050000000000000044), so
converting back gives the saved number exactly.

Every case on that metric:

- Boundaries. ``greater_than 0`` (some provider error-free) becomes
  ``less_than 1`` (some provider without errors); ``less_than 1`` (some
  provider had errors) becomes ``greater_than 0``; ``greater_than 1`` and
  ``less_than 0`` never fire and become ``less_than 0`` and ``greater_than 1``,
  which never fire either.
- Equality operators. ``ComparisonOperatorChoices`` has only greater_than and
  less_than; its >= and <= members are commented out. A row with any other
  operator never fires on either build (``_compare`` returns False) and is left
  as it is.
- A null warning (or critical) stays null.
- A threshold outside 0..1, one whose 1 - v does not convert back to the
  saved number, or one with more than six decimal places (no one types those;
  close to a share like 1/7, rounding could make the flipped comparison differ)
  has no exact conversion within the thresholds the serializer accepts (0 or
  more). Such a row is left exactly as saved, marked "earlier":
  the worker and the graph read it on the share of providers with no errors
  (``MonitorMetricsQueryBuilder(provider_error_free=True)``), so it alerts
  exactly as before. Above 1, greater_than never fires and less_than fires on
  every check with data, in either meaning.
- percentage_change. Not converted: its band depends on a live standard
  deviation, so no stored number converts it. An "earlier" row keeps the
  production band on the share with no errors
  (``tracer.utils.monitor_earlier_rule``); a "current" row uses the new metric
  and the previous-period rule.
- Soft-deleted rows are converted too, so a restored monitor alerts as before.

The mark is ``threshold_rule`` itself. It says which meaning a row's numbers
are in, and the worker reads it that way (``counts_error_free_providers``), so
a second flag would only have to agree with it. A converted row is "current",
so running this again finds nothing to convert. A row that a server still on
the previous release saves while the update rolls out gets the database
default "earlier" and is read in the old meaning, which is how it was saved.

Reverse: every static row on that metric marked "current" goes back to the old
meaning and "earlier". A converted row gets its saved operator and numbers back
exactly; a row saved after the update is converted into the old meaning for the
previous release to read.

Not covered: a threshold edit saved through a server still on the previous
release, after this runs and before the rollout ends, writes old-meaning
numbers onto a converted row. Its owner has to save the threshold again. And
an alert worker still on the previous release reads a converted row's numbers
on the share with no errors until it is replaced, so a converted monitor can
alert wrongly for the minutes between this migration and the new workers.
"""

from decimal import Decimal

from django.db import migrations, transaction

# The reviewer's GitHub login and the pull-request review-comment URL are
# filled in by the reviewer. scripts/check_update_safety.py refuses this file
# until both are real.
UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": ["postgres:tracer_useralertmonitor"],
    "mitigation": (
        "Touches only static service-provider error-rate monitors still marked "
        "'earlier' in tracer_useralertmonitor (one row per user-defined alert; "
        "not a registered high-volume table). Inside the migration's transaction "
        "it locks those rows with SELECT FOR UPDATE and rewrites operator, "
        "critical, warning and threshold_rule with one bulk_update in batches of "
        "500. No schema change and no other table. A re-run finds nothing, since "
        "converted rows are 'current'; the reverse restores the saved operator "
        "and numbers exactly. Rehearse forward and reverse in the real "
        "upgrade/restore run."
    ),
    "reviewer": "",
    "review": "",
    "large_volume_rehearsal": True,
}

PROVIDER_ERROR_RATES = "service_provider_error_rates"
STATIC = "static"
FLIPPED = {"greater_than": "less_than", "less_than": "greater_than"}
THRESHOLDS = ("critical_threshold_value", "warning_threshold_value")


def _complement(value: float) -> float:
    """1 - value, on the decimal digits the value was saved with."""
    return float(Decimal(1) - Decimal(repr(value)))


# With at most six decimal places a threshold v is never closer to a provider
# share a/n than 1 / (n * 10**6), far wider than float rounding, so a/n op v and
# (n - a)/n flipped-op (1 - v) always agree.
MAX_DECIMAL_PLACES = 6


def _converts_exactly(value: float | None) -> bool:
    """A share's threshold that flips exactly: in 0..1, at most six decimal
    places, and whose complement converts back to it."""
    if value is None:
        return True
    places = -Decimal(repr(value)).normalize().as_tuple().exponent
    return (
        0 <= value <= 1
        and places <= MAX_DECIMAL_PLACES
        and _complement(_complement(value)) == value
    )


def _convert(apps, schema_editor, from_rule: str, to_rule: str) -> None:
    UserAlertMonitor = apps.get_model("tracer", "UserAlertMonitor")
    alias = schema_editor.connection.alias
    # The base manager hides no rows: soft-deleted monitors are converted too.
    monitors = UserAlertMonitor._base_manager.using(alias)
    with transaction.atomic(using=alias):
        rows = (
            monitors.select_for_update()
            .filter(
                metric_type=PROVIDER_ERROR_RATES,
                threshold_type=STATIC,
                threshold_rule=from_rule,
                threshold_operator__in=tuple(FLIPPED),
            )
            .order_by("pk")
        )
        changed = []
        for row in rows:
            values = [getattr(row, name) for name in THRESHOLDS]
            if not all(_converts_exactly(value) for value in values):
                continue
            row.threshold_operator = FLIPPED[row.threshold_operator]
            for name, value in zip(THRESHOLDS, values, strict=True):
                setattr(row, name, None if value is None else _complement(value))
            row.threshold_rule = to_rule
            changed.append(row)
        monitors.bulk_update(
            changed,
            ["threshold_operator", *THRESHOLDS, "threshold_rule"],
            batch_size=500,
        )


def convert_provider_thresholds(apps, schema_editor) -> None:
    """Old-meaning static rows to the share with errors, marked current."""
    _convert(apps, schema_editor, "earlier", "current")


def restore_provider_thresholds(apps, schema_editor) -> None:
    """Current static rows back to the share with no errors, marked earlier."""
    _convert(apps, schema_editor, "current", "earlier")


class Migration(migrations.Migration):
    dependencies = [("tracer", "0133_useralertmonitor_threshold_rule")]

    operations = [
        migrations.RunPython(convert_provider_thresholds, restore_provider_thresholds),
    ]
