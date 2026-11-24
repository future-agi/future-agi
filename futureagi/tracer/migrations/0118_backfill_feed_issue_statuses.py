"""Provision escalation policies and recover Omega finding times.

Issue statuses are kept. Open legacy-owned issues join automation at runtime
(see feed_escalation.AUTOMATIC_ORIGINS), so this migration never changes what
users see and old pods writing during the rollout are covered the same way.
"""

from django.db import migrations
from django.db.models import OuterRef, Subquery

BATCH = 100


def backfill_feed_issue_statuses(apps, schema_editor):
    alias = schema_editor.connection.alias
    PolicyRow = apps.get_model("tracer", "FeedEscalationPolicy")
    Workspace = apps.get_model("accounts", "Workspace")
    Membership = apps.get_model("tracer", "ErrorClusterTraces")
    Finding = apps.get_model("tracer", "TraceInvestigationFinding")
    for workspace_id in (
        Workspace.objects.using(alias)
        .filter(deleted=False)
        .values_list("id", flat=True)
        .iterator(chunk_size=BATCH)
    ):
        PolicyRow.objects.using(alias).get_or_create(workspace_id=workspace_id)
    # Scanner/eval source times need external lookups, so runtime retries repair them.
    source_time = (
        Finding.objects.using(alias)
        .filter(pk=OuterRef("finding_id"))
        .values("report__job__root_end_time")[:1]
    )
    pending = Membership.objects.using(alias).filter(
        occurred_at__isnull=True,
        finding__report__job__root_end_time__isnull=False,
    )
    cursor = None
    while True:
        batch = pending if cursor is None else pending.filter(pk__gt=cursor)
        ids = list(batch.order_by("pk").values_list("pk", flat=True)[:BATCH])
        if not ids:
            break
        pending.filter(pk__in=ids).update(
            occurred_at=Subquery(source_time), occurrence_time_source="omega_root_end"
        )
        cursor = ids[-1]


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("tracer", "0117_feed_escalation_policy"),
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]
    operations = [
        migrations.RunPython(backfill_feed_issue_statuses, migrations.RunPython.noop)
    ]
