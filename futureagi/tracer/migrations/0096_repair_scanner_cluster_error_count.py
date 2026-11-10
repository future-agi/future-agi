"""Repair feed occurrence counts inflated by the non-idempotent increment.

``assign_to_cluster`` incremented ``error_count`` on every assignment while the
two writes it counted — the issue's cluster FK and the junction row — were both
idempotent. Re-scanning a trace therefore bumped the "occurrences" the Feed
renders without adding a member. Measured on live data: a fifth of rendered
scanner entries were wrong, the worst reading 33 occurrences against 3 issues
across 3 traces, while the trace count beside it stayed correct because it was
already derived rather than incremented.

The code now derives both counts, so this repairs the rows written before that.

Only scanner clusters that still have issue rows are touched. Eval clusters
carry no ``TraceScanIssue`` rows at all — their members live in the junction —
so recomputing from issues would zero them. Scanner clusters with zero issues
are legacy pre-revamp rows that ``_base_qs`` already excludes from the feed;
leaving them alone keeps this migration to the rows a user can actually see.

Keyset-paginated + ``atomic = False`` so each batch commits independently. The
repair skips rows already correct, so a run interrupted halfway resumes rather
than restarting, and re-running is a no-op.
"""

from django.db import migrations
from django.db.models import Count, Q

_BATCH = 500


def repair_error_counts(apps, schema_editor):
    TraceErrorGroup = apps.get_model("tracer", "TraceErrorGroup")

    base_qs = (
        TraceErrorGroup.objects.filter(source="scanner", deleted=False)
        .annotate(
            actual=Count(
                "scan_issues",
                filter=Q(scan_issues__deleted=False),
                distinct=True,
            )
        )
        .exclude(actual=0)
    )

    # Keyset pagination by pk: each batch is a fresh query and the write commits
    # between queries (atomic = False), so nothing writes into an open cursor.
    # iterator() would hold a server-side cursor across those commits.
    last_id = None
    while True:
        chunk_qs = base_qs.order_by("id")
        if last_id is not None:
            chunk_qs = chunk_qs.filter(id__gt=last_id)
        batch = list(chunk_qs[:_BATCH])
        if not batch:
            break
        last_id = batch[-1].id
        stale = [c for c in batch if c.error_count != c.actual]
        if stale:
            for cluster in stale:
                cluster.error_count = cluster.actual
            TraceErrorGroup.objects.bulk_update(
                stale, ["error_count"], batch_size=len(stale)
            )


class Migration(migrations.Migration):
    # Outside a transaction so each batch commits independently — this repair
    # touches every scanner cluster in the install and must not hold one long
    # write lock, and a re-run resumes from what already committed.
    atomic = False

    dependencies = [
        ("tracer", "0095_merge_20260722_1400"),
    ]

    # The old values are drift, not data — there is nothing to restore them to.
    operations = [
        migrations.RunPython(repair_error_counts, migrations.RunPython.noop),
    ]
