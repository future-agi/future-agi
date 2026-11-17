"""The command that runs 0140's session project repair again, unrecorded.

PostgreSQL is real; only the ClickHouse client is stubbed at its boundary,
as 0140's own test does, so the real session resolver and 0117's batch step
run. 0140 itself is applied to the historical state at its predecessor, as
migrate applies it.
"""

import io
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, connections
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone

from accounts.models.organization import Organization
from model_hub.models.annotation_queues import QueueItem
from model_hub.tests.test_migration_rebackfill_queueitem_project_upgraded import (
    TARGET,
    _apply_upgrade,
    _guard_reads,
    _item,
    _original,
    _session_rows,
)
from model_hub.tests.test_migration_rebackfill_queueitem_project_upgraded import (
    migration_loader as migration_loader,
)
from model_hub.tests.test_migration_rebackfill_queueitem_project_upgraded import (
    queue_context as queue_context,
)
from tracer.models.project import Project
from tracer.services.clickhouse.v2 import trace_session_dict_reader as session_reader

COMMAND = "rebackfill_queueitem_session_project"
pytestmark = pytest.mark.django_db(
    transaction=True, databases={"default", "default_direct"}
)


def clickhouse_down(monkeypatch):
    def unreachable():
        raise ConnectionError("ClickHouse is unreachable")

    monkeypatch.setattr(session_reader, "_get_client", unreachable)


def failed_lookups(batches):
    """The command's error for ``batches`` failed lookups."""
    word = "batch" if batches == 1 else "batches"
    return (
        f"The ClickHouse lookup failed for {batches} {word} of trace sessions, so "
        "their queue items still have no project. Run this command again; it "
        "stamps only the items that still have none."
    )


def clickhouse_failing_after(monkeypatch, projects, calls):
    """ClickHouse answers the first ``calls`` lookups, then fails every one."""
    answered = []

    def query(sql, *, parameters):
        if len(answered) >= calls:
            raise ConnectionError("the ClickHouse connection was lost")
        answered.append(parameters["ids"])
        return SimpleNamespace(
            result_rows=[
                (sid, sid, "Synthetic session", timezone.now(), str(projects[sid]))
                for sid in parameters["ids"]
                if sid in projects
            ]
        )

    client = SimpleNamespace(query=Mock(side_effect=query))
    monkeypatch.setattr(session_reader, "_get_client", lambda: client)
    return client


def run_command():
    out, err = io.StringIO(), io.StringIO()
    error = None
    try:
        call_command(COMMAND, stdout=out, stderr=err)
    except CommandError as exc:
        error = exc
    return out.getvalue(), err.getvalue(), error


def other_org_project(queue_context):
    other = Organization.objects.create(name=f"Other org {uuid.uuid4()}")
    return Project.objects.create(
        name=f"Other project {uuid.uuid4()}",
        organization=other,
        workspace_id=queue_context.project.workspace_id,
        model_type="GenerativeLLM",
        trace_type="observe",
    )


def mixed_items(queue_context):
    """One item of each outcome: stamped, unresolved, other org, ineligible."""
    return {
        "resolvable": _item(queue_context),
        "unresolved": _item(queue_context),
        "other_org": _item(queue_context),
        "trace": _item(queue_context, source_type="trace", trace_id=uuid.uuid4()),
    }


def session_projects(queue_context, items):
    return {
        str(items["resolvable"].trace_session_id): queue_context.project.id,
        str(items["other_org"].trace_session_id): other_org_project(queue_context).id,
    }


def project_of(item):
    rows = QueueItem.objects.filter(pk=item.pk)
    return rows.values_list("project_id", flat=True).get()


def projects_of(items):
    return {name: project_of(item) for name, item in items.items()}


def test_clickhouse_down_at_migrate_stamps_nothing_and_0140_is_recorded(
    migration_loader, queue_context, monkeypatch
):
    item = _item(queue_context)
    clickhouse_down(monkeypatch)

    # 0140 keeps going when ClickHouse fails, so migrate records it.
    _apply_upgrade(migration_loader)

    item.refresh_from_db()
    assert item.project_id is None
    assert (
        MigrationRecorder(connection)
        .migration_qs.filter(app=TARGET[0], name=TARGET[1])
        .exists()
    )


def test_the_command_fails_while_clickhouse_is_still_unreachable(
    queue_context, monkeypatch, capsys
):
    item = _item(queue_context)
    clickhouse_down(monkeypatch)

    out, err, error = run_command()

    item.refresh_from_db()
    assert item.project_id is None
    assert error is not None
    assert str(error) == failed_lookups(1)
    assert out.splitlines() == [
        "stamped=0 unresolvable=1 org_mismatch=0 failed_batches=1"
    ]
    assert err == ""
    # 0117's own line about the failed lookup is not printed.
    assert capsys.readouterr().out == ""


def test_once_clickhouse_is_up_the_command_stamps_what_0140_would_have(
    migration_loader, queue_context, monkeypatch
):
    items = mixed_items(queue_context)
    projects = session_projects(queue_context, items)
    _session_rows(monkeypatch, projects)

    with _guard_reads("default"):
        out, err, error = run_command()

    assert error is None
    assert out.splitlines() == [
        "stamped=1 unresolvable=1 org_mismatch=1 failed_batches=0"
    ]
    assert err == ""
    by_command = projects_of(items)
    assert by_command == {
        "resolvable": queue_context.project.id,
        "unresolved": None,
        "other_org": None,
        "trace": None,
    }

    # 0140 itself, on the same rows from the same start, stamps the same.
    QueueItem.objects.filter(pk=items["resolvable"].pk).update(project=None)
    _apply_upgrade(migration_loader)
    assert projects_of(items) == by_command


def test_a_second_run_changes_nothing(queue_context, monkeypatch):
    items = mixed_items(queue_context)
    client = _session_rows(monkeypatch, session_projects(queue_context, items))
    run_command()
    before = {
        name: QueueItem.objects.filter(pk=item.pk).values().get()
        for name, item in items.items()
    }
    writes = []

    def record(execute, sql, params, many, context):
        if sql.lstrip().upper().startswith("UPDATE"):
            writes.append(sql)
        return execute(sql, params, many, context)

    with connections["default"].execute_wrapper(record):
        out, err, error = run_command()

    assert error is None
    assert writes == []
    assert out.splitlines() == [
        "stamped=0 unresolvable=1 org_mismatch=1 failed_batches=0"
    ]
    after = {
        name: QueueItem.objects.filter(pk=item.pk).values().get()
        for name, item in items.items()
    }
    assert after == before
    # The stamped item is no longer selected; the others are looked up again.
    looked_up = client.query.call_args_list[-1].kwargs["parameters"]["ids"]
    assert str(items["resolvable"].trace_session_id) not in looked_up


def test_nothing_to_repair_prints_zero_counts(queue_context, monkeypatch):
    client = _session_rows(monkeypatch, {})

    out, err, error = run_command()

    assert error is None
    assert out.splitlines() == [
        "stamped=0 unresolvable=0 org_mismatch=0 failed_batches=0"
    ]
    assert err == ""
    client.query.assert_not_called()


def test_a_lookup_failing_midway_keeps_what_was_stamped_and_a_rerun_finishes(
    queue_context, monkeypatch
):
    items = sorted((_item(queue_context) for _ in range(5)), key=lambda i: i.pk)
    projects = {str(item.trace_session_id): queue_context.project.id for item in items}
    monkeypatch.setattr(_original, "_BATCH", 2)
    clickhouse_failing_after(monkeypatch, projects, calls=1)

    out, err, error = run_command()

    # The first batch was stamped; the two after it failed and stay empty.
    assert str(error) == failed_lookups(2)
    assert out.splitlines() == [
        "stamped=2 unresolvable=3 org_mismatch=0 failed_batches=2"
    ]
    first_batch_only = [queue_context.project.id] * 2 + [None] * 3
    assert [project_of(item) for item in items] == first_batch_only

    clickhouse_failing_after(monkeypatch, projects, calls=99)
    out, err, error = run_command()

    assert error is None
    assert out.splitlines() == [
        "stamped=3 unresolvable=0 org_mismatch=0 failed_batches=0"
    ]
    assert [project_of(item) for item in items] == [queue_context.project.id] * 5
