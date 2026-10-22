"""Reset the legacy Escalating default; let runtime checks confirm new growth.

Legacy user-selected Escalating values intentionally reset too: the old schema
cannot distinguish those from its creation default. Other statuses are retained.
"""

from django.db import migrations
from django.db.models import F, OuterRef, Subquery
from django.utils import timezone

BATCH = 100


def backfill_feed_issue_statuses(apps, schema_editor):
    alias = schema_editor.connection.alias
    PolicyRow = apps.get_model("tracer", "FeedEscalationPolicy")
    Workspace = apps.get_model("accounts", "Workspace")
    Issue = apps.get_model("tracer", "TraceErrorGroup")
    Membership = apps.get_model("tracer", "ErrorClusterTraces")
    Finding = apps.get_model("tracer", "TraceInvestigationFinding")
    now = timezone.now()
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
    pending = Issue.objects.using(alias).filter(
        deleted=False,
        target_type="error_feed",
        status="escalating",
        status_origin="legacy_unknown",
    )
    cursor, reset = None, 0
    while True:
        batch = pending if cursor is None else pending.filter(pk__gt=cursor)
        ids = list(batch.order_by("pk").values_list("pk", flat=True)[:BATCH])
        if not ids:
            break
        # Recheck ownership/status at write time so a concurrent new manual edit wins.
        reset += pending.filter(pk__in=ids).update(
            status="for_review",
            status_origin="system",
            status_revision=F("status_revision") + 1,
            status_changed_at=now,
            status_changed_by_id=None,
            escalation_state={},
            escalation_next_evaluation_at=now,
            updated_at=now,
        )
        cursor = ids[-1]
    print(f"[0118-feed-escalation] reset_legacy_escalating={reset}")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [
        ("tracer", "0117_feed_escalation_policy"),
        ("tracer", "0117_merge_error_feed_and_simulation_project"),
    ]
    operations = [
        migrations.RunPython(backfill_feed_issue_statuses, migrations.RunPython.noop)
    ]
