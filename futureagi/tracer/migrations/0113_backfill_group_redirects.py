"""Recover old group redirects and soft-delete retired F6 groups."""

import uuid

from django.db import migrations
from django.utils import timezone


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _resolve(value, created):
    return _uuid(created.get(str(value), value))


def _member_count(part):
    ids = part.get("occurrence_ids")
    return len(ids) if isinstance(ids, list) else -1


def backfill_redirects(apps, schema_editor):
    Decision = apps.get_model("tracer", "TraceGroupingDecision")
    Group = apps.get_model("tracer", "TraceErrorGroup")
    Scope = apps.get_model("tracer", "TraceGroupingScope")
    State = apps.get_model("tracer", "TraceGroupingIssueState")
    database = schema_editor.connection.alias
    pending = []

    def flush():
        if not pending:
            return
        group_ids = {
            group_id for _, source, target in pending for group_id in (source, target)
        }
        projects = dict(
            Group.objects.using(database)
            .filter(pk__in=group_ids)
            .values_list("id", "project_id")
        )
        scope_projects = dict(
            Scope.objects.using(database)
            .filter(pk__in={scope_id for scope_id, _, _ in pending})
            .values_list("id", "project_id")
        )
        for scope_id, source, target in pending:
            if projects.get(source) is not None and projects.get(
                source
            ) == projects.get(target) == scope_projects.get(scope_id):
                Group.objects.using(database).filter(
                    pk=source, redirect_to__isnull=True
                ).update(redirect_to_id=target)
        pending.clear()

    decisions = (
        Decision.objects.using(database)
        .order_by("scope_id", "registry_revision", "id")
        .only("id", "scope_id", "commands", "result")
    )
    for decision in decisions.iterator(chunk_size=500):
        created = (
            decision.result.get("created_issue_ids", {})
            if isinstance(decision.result, dict)
            else {}
        )
        if not isinstance(created, dict):
            continue
        for command in decision.commands if isinstance(decision.commands, list) else []:
            if not isinstance(command, dict):
                continue
            if command.get("type") == "merge":
                target = _resolve(
                    command.get("survivor_issue_id", command.get("temporary_id")),
                    created,
                )
                sources = command.get("source_issue_ids", [])
                if not isinstance(sources, list):
                    continue
                for value in sources:
                    source = _resolve(value, created)
                    if source and target and source != target:
                        pending.append((decision.scope_id, source, target))
            elif command.get("type") == "split":
                source = _resolve(command.get("issue_id"), created)
                parts = command.get("parts", [])
                if not source or not isinstance(parts, list) or not parts:
                    continue
                parts = [part for part in parts if isinstance(part, dict)]
                if not parts:
                    continue
                selected = max(parts, key=_member_count)
                target = _resolve(selected.get("temporary_id"), created)
                if target and source != target:
                    pending.append((decision.scope_id, source, target))
        if len(pending) >= 500:
            flush()
    flush()

    now = timezone.now()
    retired = State.objects.using(database).filter(retired=True, deleted=False)
    while True:
        ids = list(retired.values_list("cluster_id", flat=True)[:500])
        if not ids:
            break
        Group.objects.using(database).filter(pk__in=ids, deleted=False).update(
            deleted=True, deleted_at=now, updated_at=now
        )
        State.objects.using(database).filter(cluster_id__in=ids).update(
            deleted=True, deleted_at=now, updated_at=now
        )


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("tracer", "0112_error_group_redirect")]
    operations = [migrations.RunPython(backfill_redirects, migrations.RunPython.noop)]
