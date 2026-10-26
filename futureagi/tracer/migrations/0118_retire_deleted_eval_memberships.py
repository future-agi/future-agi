"""Retire Error Feed memberships whose eval result was deleted; recount eval issues.

Every path that soft-deletes an eval result now retires its membership
(``soft_delete_eval_results``). Rows deleted before that left the membership
live, so an issue listed traces whose eval no longer exists and counted them.
This retires those memberships and derives each eval issue's counters from its
live members, as ``assign_to_cluster`` now does. 0096 repaired scanner counts
only, so eval counts also carry the old increment drift; this fixes that too.

An issue left with no members is soft-deleted unless a person owns it. Its
centroid stays, so a recurrence revives it. Nothing is hard-deleted.

Session memberships are left alone: ``(cluster, trace_session)`` is unique
across deleted rows, so a retired session member would block the session from
re-joining its issue.

Keyset-paginated with ``atomic = False`` so each batch commits on its own.
Rows already correct are skipped, so a re-run is a no-op.
"""

from django.db import migrations
from django.db.models import Avg, Count, Max, Min, Q
from django.utils import timezone

_BATCH = 200


def _impact(avg_score):
    """Mirrors ``eval_clustering._score_to_impact`` at the time of writing."""
    if avg_score is None:
        return "MEDIUM"
    if avg_score < 0.3:
        return "HIGH"
    if avg_score < 0.6:
        return "MEDIUM"
    return "LOW"


def _owned_by_a_person(issue):
    """Mirrors ``eval_clustering._owned_by_a_person`` at the time of writing."""
    return bool(
        issue.assignee_id
        or issue.external_issue_url
        or issue.external_issue_id
        or issue.status not in ("escalating", "for_review")
    )


def retire_deleted_eval_memberships(apps, schema_editor):
    TraceErrorGroup = apps.get_model("tracer", "TraceErrorGroup")
    ErrorClusterTraces = apps.get_model("tracer", "ErrorClusterTraces")
    now = timezone.now()

    # Historical managers do not hide soft-deleted rows, so every filter is explicit.
    stale = ErrorClusterTraces.objects.filter(
        deleted=False,
        trace__isnull=False,
        eval_logger__deleted=True,
        cluster__source="eval",
        cluster__deleted=False,
    )
    touched = set(stale.values_list("cluster_id", flat=True))
    stale.update(deleted=True, deleted_at=now)

    live = Q(clusters__deleted=False)
    issues = TraceErrorGroup.objects.filter(source="eval", deleted=False).annotate(
        occurrences=Count(
            "clusters", filter=live & Q(clusters__eval_logger__isnull=False)
        ),
        traces=Count("clusters__trace", filter=live, distinct=True),
        sessions=Count("clusters__trace_session", filter=live, distinct=True),
        first=Min("clusters__created_at", filter=live),
        last=Max("clusters__created_at", filter=live),
        avg_score=Avg(
            "clusters__eval_logger__output_float",
            filter=live & Q(clusters__eval_logger__isnull=False),
        ),
    )
    fields = [
        "error_count",
        "total_events",
        "unique_traces",
        "first_seen",
        "last_seen",
        "combined_impact",
        "deleted",
        "deleted_at",
    ]
    last_id = None
    while True:
        batch_qs = issues.order_by("id")
        if last_id is not None:
            batch_qs = batch_qs.filter(id__gt=last_id)
        batch = list(batch_qs[:_BATCH])
        if not batch:
            break
        last_id = batch[-1].id
        changed = []
        for issue in batch:
            units = (
                issue.sessions if issue.eval_target_type == "session" else issue.traces
            )
            before = (issue.error_count, issue.total_events, issue.unique_traces)
            issue.error_count = issue.total_events = issue.occurrences
            issue.unique_traces = units
            dirty = issue.id in touched or before != (
                issue.error_count,
                issue.total_events,
                units,
            )
            # Only issues whose counts moved get new dates and impact: elsewhere
            # last_seen was stamped just after its row and would churn for nothing.
            # Keying on the counts too lets a re-run finish an interrupted repair.
            if dirty:
                if issue.first is not None:
                    issue.first_seen, issue.last_seen = issue.first, issue.last
                issue.combined_impact = _impact(issue.avg_score)
            empty = issue.occurrences == 0 and units == 0
            if empty and not _owned_by_a_person(issue):
                issue.deleted, issue.deleted_at = True, now
                dirty = True
            if dirty:
                changed.append(issue)
        if changed:
            TraceErrorGroup.objects.bulk_update(
                changed, fields, batch_size=len(changed)
            )


class Migration(migrations.Migration):
    # Outside a transaction so each batch commits on its own, and a re-run
    # resumes from what already committed.
    atomic = False

    dependencies = [
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]

    # The retired rows and old counts were drift, not data: nothing to restore.
    operations = [
        migrations.RunPython(
            retire_deleted_eval_memberships, migrations.RunPython.noop
        ),
    ]
