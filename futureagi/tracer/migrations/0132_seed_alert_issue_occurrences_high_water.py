"""Seed each occurrences alert's high-water mark from the last alert it queued.

0120 added ``occurrences_high_water`` with a default of 0 and no backfill, and
0119 recounts eval issues, which can lower an issue's count. An occurrences rule
that had already alerted for an issue therefore alerted again when the lowered
count crossed the same threshold. The live writer stores the count of every
occurrences alert it queues; this copies that count from ``last_event``, the
event whose delivery the state row last queued.

Update safety:

- Reads ``tracer_error_feed_alert_issue_state`` rows still at 0 that have a
  ``last_event``, joined by primary key to their rule (trigger type only) and
  to that event (its ``after`` snapshot). The read on the event table is
  bounded by the number of state rows.
- Writes only ``occurrences_high_water``, only on rows of occurrences rules that
  are still at 0, and only a positive integer that fits the column. Other rule
  types, rows without ``last_event`` and counts that are missing, not integers
  or not positive stay at 0. A value already above 0 is never changed.
- Safe to re-run: a seeded row is no longer at 0, so a second or resumed run
  selects nothing for it. Keyset pages with ``atomic = False`` commit one page
  at a time.
- Touches no schema. The reverse is a no-op.
"""

from django.db import migrations

_BATCH = 500
_OCCURRENCES_CROSSED = "occurrences_crossed"
# PositiveIntegerField's upper bound on PostgreSQL.
_COLUMN_MAX = 2_147_483_647

UPDATE_SAFETY = {
    "schema_version": 1,
    "risk_classes": ["data_migration"],
    "tables": [
        "postgres:tracer_error_feed_alert_issue_state",
        "postgres:tracer_error_feed_alert_rule",
        "postgres:tracer_error_feed_issue_event",
    ],
    "mitigation": (
        "Seeds tracer_error_feed_alert_issue_state.occurrences_high_water for rows "
        "still at 0 whose rule is an occurrences_crossed rule, from the positive "
        "integer after['occurrences'] of the row's last_event. Reads the state rows "
        "in id-ordered keyset pages of 500, joined by primary key to "
        "tracer_error_feed_alert_rule and tracer_error_feed_issue_event, so the read "
        "on the registered event table is bounded by the number of state rows. Each "
        "page is one bulk_update whose UPDATE also requires occurrences_high_water = "
        "0, so a value already set is never lowered or replaced. atomic=False "
        "commits each page; a re-run or a resumed run is a no-op for seeded rows, "
        "and the reverse is a no-op. No schema change; only the updated rows are "
        "locked, one page at a time."
    ),
    "reviewer": "<actual-github-login>",
    "review": "<actual-PR-review-discussion-permalink>",
    "large_volume_rehearsal": True,
}


def _alerted_count(after):
    """The occurrence count an alert was queued at, or None when it is unusable."""
    if not isinstance(after, dict):
        return None
    count = after.get("occurrences")
    # type() rather than isinstance(): JSON true decodes to bool, a subclass of int.
    if type(count) is not int or not 0 < count <= _COLUMN_MAX:
        return None
    return count


def seed_occurrences_high_water(apps, schema_editor):
    State = apps.get_model("tracer", "ErrorFeedAlertIssueState")
    alias = schema_editor.connection.alias

    pending = (
        State.objects.using(alias)
        .filter(
            occurrences_high_water=0,
            last_event__isnull=False,
            rule__trigger_type=_OCCURRENCES_CROSSED,
        )
        .order_by("id")
    )
    # Keyset pages rather than iterator(): with atomic = False each page commits,
    # and iterator() would hold a server-side cursor across those commits.
    last_id = None
    while True:
        page = pending if last_id is None else pending.filter(id__gt=last_id)
        rows = list(page.values_list("id", "last_event__after")[:_BATCH])
        if not rows:
            break
        last_id = rows[-1][0]
        seeded = []
        for state_id, after in rows:
            count = _alerted_count(after)
            if count is not None:
                seeded.append(State(id=state_id, occurrences_high_water=count))
        if seeded:
            # bulk_update keeps this queryset's filter in its UPDATE, so a row a
            # worker raised after this page was read is left as the worker set it.
            State.objects.using(alias).filter(occurrences_high_water=0).bulk_update(
                seeded, ["occurrences_high_water"], batch_size=len(seeded)
            )


class Migration(migrations.Migration):
    # Outside a transaction so each page commits on its own, and a re-run
    # resumes from what already committed.
    atomic = False

    dependencies = [
        ("tracer", "0131_merge_c26_dev_1109"),
    ]

    # A seeded mark is what the live writer records when it queues an
    # occurrences alert: there is nothing to restore.
    operations = [
        migrations.RunPython(seed_occurrences_high_water, migrations.RunPython.noop),
    ]
